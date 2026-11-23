"""Notification delivery.

* Announcements are delivered by creating one inbox row (UserNotification)
  per recipient. "Send now" delivers immediately; scheduled ones are delivered
  by dispatch_due() once their time has come.
* Event reminders (configured per event) are turned into reminder
  notifications by dispatch_due() when their time comes.

dispatch_due() is safe to run often and from several places at once: each
notification/reminder is claimed with a conditional UPDATE, so it can only be
delivered once. It runs from `flask notifications worker`, from
`flask notifications dispatch` (e.g. a scheduled task), from the "Process due
now" button, and briefly whenever someone opens their inbox.
"""

import logging
from datetime import timedelta

from sqlalchemy import insert, select, update

from ..extensions import db
from ..models import Event, EventReminder, Notification, SystemSetting, User, UserNotification
from ..models.base import utcnow
from .mailer import email_configured, send_emails
from .timezone import local_tz, to_local

log = logging.getLogger(__name__)

# A reminder that comes due after its event has already started is no longer
# useful (e.g. the server was off). "At the start time" reminders get a short grace period.
START_GRACE = timedelta(minutes=60)
REMINDER_LOOKAHEAD = timedelta(days=31)


def recipient_ids(audience, role_id=None, user_id=None):
    stmt = select(User.id).where(User.is_active.is_(True))
    if audience == "role":
        stmt = stmt.where(User.role_id == role_id)
    elif audience == "user":
        stmt = stmt.where(User.id == user_id)
    return list(db.session.scalars(stmt))


def _fan_out(notification_id, user_ids, now):
    if user_ids:
        db.session.execute(insert(UserNotification), [
            {"notification_id": notification_id, "user_id": uid, "delivered_at": now} for uid in user_ids
        ])
    return len(user_ids)


def deliver(notification, now=None, allowed_statuses=("draft", "scheduled")):
    """Deliver a notification to its audience. Returns the number of recipients,
    or None if it was already delivered (or cancelled) by someone else."""
    now = now or utcnow()
    claimed = db.session.execute(
        update(Notification)
        .where(Notification.id == notification.id, Notification.status.in_(allowed_statuses))
        .values(status="sent", sent_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if claimed != 1:
        return None
    count = _fan_out(notification.id, recipient_ids(
        notification.audience, notification.audience_role_id, notification.audience_user_id), now)
    db.session.refresh(notification)
    return count


def _reminder_text(event, tz):
    start = to_local(event.start_at, tz)
    when = start.strftime("%d %b %Y") if event.all_day else start.strftime("%d %b %Y, %H:%M")
    title = f"Reminder: {event.title}"
    body = f"{event.title} starts {when}" + (f" at {event.location}." if event.location else ".")
    return title, body


def _deliver_reminder(reminder, event, now, tz, stats):
    title, body = _reminder_text(event, tz)
    notification = Notification(title=title, body=body, type="reminder", audience="all",
                                status="sent", sent_at=now, event_id=event.id)
    db.session.add(notification)
    db.session.flush()
    user_ids = recipient_ids("all")
    _fan_out(notification.id, user_ids, now)
    stats["reminders"] += 1

    if reminder.channel == "email":
        if email_configured():
            emails = list(db.session.scalars(select(User.email).where(User.id.in_(user_ids))))
            sent, failed = send_emails(emails, title, body)
            stats["emails_sent"] += sent
            stats["emails_failed"] += failed
        else:
            stats["emails_skipped"] += 1


def dispatch_due(now=None):
    """Deliver everything that is due. Returns counts of what happened."""
    now = now or utcnow()
    stats = {"announcements": 0, "recipients": 0, "reminders": 0, "expired_reminders": 0,
             "emails_sent": 0, "emails_failed": 0, "emails_skipped": 0}

    due = db.session.scalars(select(Notification).where(
        Notification.status == "scheduled", Notification.scheduled_at <= now)).all()
    for notification in due:
        count = deliver(notification, now, allowed_statuses=("scheduled",))
        if count is not None:
            stats["announcements"] += 1
            stats["recipients"] += count
    db.session.commit()

    tz = local_tz()
    candidates = db.session.execute(
        select(EventReminder, Event).join(Event, EventReminder.event_id == Event.id)
        .where(EventReminder.is_sent.is_(False), Event.status.in_(("upcoming", "ongoing")),
               Event.start_at <= now + REMINDER_LOOKAHEAD)
    ).all()
    for reminder, event in candidates:
        if event.start_at - timedelta(minutes=reminder.offset_minutes) > now:
            continue  # not due yet
        claimed = db.session.execute(
            update(EventReminder).where(EventReminder.id == reminder.id, EventReminder.is_sent.is_(False))
            .values(is_sent=True, sent_at=now).execution_options(synchronize_session=False)
        ).rowcount
        if claimed != 1:
            continue
        grace = START_GRACE if reminder.offset_minutes == 0 else timedelta(0)
        if now > event.start_at + grace:
            stats["expired_reminders"] += 1  # too late to be useful: marked done, not delivered
        else:
            _deliver_reminder(reminder, event, now, tz, stats)
        db.session.commit()

    if stats["announcements"] or stats["reminders"] or stats["expired_reminders"]:
        log.info("Notification dispatch: %s", stats)
    _record_run(now, stats)
    return stats


def _record_run(now, stats):
    row = db.session.get(SystemSetting, "notifications.last_dispatch")
    value = {"at": now.isoformat(timespec="seconds") + "Z", "stats": stats}
    if row is None:
        db.session.add(SystemSetting(key="notifications.last_dispatch", value=value))
    else:
        row.value = value
    db.session.commit()


def last_run():
    row = db.session.get(SystemSetting, "notifications.last_dispatch")
    return row.value if row else None


def dispatch_quietly(min_interval=timedelta(seconds=30)):
    """Run dispatch (at most every 30 s) without ever breaking the caller.
    Used when users open their inbox, so delivery happens even without a worker."""
    from datetime import datetime
    try:
        previous = last_run()
        if previous:
            at = datetime.fromisoformat(previous["at"].rstrip("Z"))
            if utcnow() - at < min_interval:
                return
        dispatch_due()
    except Exception:  # noqa: BLE001 - delivery problems must not break page loads
        db.session.rollback()
        log.exception("Notification dispatch failed")
