"""Notifications API: announcements (send now / schedule / draft), delivery
stats, upcoming event reminders and a manual "process due now" trigger."""

from datetime import timedelta

from flask import jsonify, request
from flask_login import current_user
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import selectinload

from ...auth.security import permission_required
from ...extensions import db
from ...models import Event, EventReminder, Notification, Role, User, UserNotification
from ...models.base import utcnow
from ...services.audit import log_activity
from ...services.mailer import email_configured
from ...services.markdown_render import render_markdown
from ...services.notifications import deliver, dispatch_due, last_run, recipient_ids
from ...services.timezone import local_tz, to_local, to_utc
from . import bp
from .schemas import NotificationIn
from .utils import ApiError, iso, paginate, parse_body, search_term

READ = "notifications.read"
WRITE = "notifications.write"
EDITABLE = ("draft", "scheduled", "cancelled")


def _local(dt, tz):
    return to_local(dt, tz).strftime("%Y-%m-%dT%H:%M") if dt else None


def _stats():
    """{notification_id: (recipients, read)} for all notifications that have deliveries."""
    rows = db.session.execute(
        select(UserNotification.notification_id, func.count(),
               func.sum(case((UserNotification.read_at.is_not(None), 1), else_=0)))
        .group_by(UserNotification.notification_id)
    ).all()
    return {nid: (total, int(read or 0)) for nid, total, read in rows}


def notification_json(n, tz, stats=None):
    recipients, read = (stats or {}).get(n.id, (0, 0)) if stats is not None else _stats().get(n.id, (0, 0))
    audience_label = {"all": "Everyone"}.get(n.audience)
    if n.audience == "role":
        audience_label = f"Role: {n.audience_role.name.replace('_', ' ')}" if n.audience_role else "Role (deleted)"
    elif n.audience == "user":
        audience_label = n.audience_user.full_name if n.audience_user else "Person (deleted)"
    return {
        "id": n.id,
        "title": n.title,
        "body": n.body,
        "body_html": render_markdown(n.body),
        "type": n.type,
        "audience": n.audience,
        "audience_role_id": n.audience_role_id,
        "audience_user_id": n.audience_user_id,
        "audience_label": audience_label,
        "audience_user": ({"id": n.audience_user.id, "name": n.audience_user.full_name, "email": n.audience_user.email}
                          if n.audience_user else None),
        "status": n.status,
        "scheduled_at": iso(n.scheduled_at),
        "scheduled_local": _local(n.scheduled_at, tz),
        "sent_at": iso(n.sent_at),
        "event": {"id": n.event.id, "title": n.event.title} if n.event else None,
        "recipients": recipients,
        "read": read,
        "editable": n.status in EDITABLE,
        "created_by": n.created_by.full_name if n.created_by else ("System" if n.type == "reminder" else None),
        "created_at": iso(n.created_at),
    }


def get_notification(notification_id):
    notification = db.session.get(Notification, notification_id)
    if notification is None:
        raise ApiError("Notification not found.", 404)
    return notification


def snapshot(n):
    return {"title": n.title, "body": n.body, "type": n.type, "audience": n.audience,
            "audience_role_id": n.audience_role_id, "audience_user_id": n.audience_user_id,
            "status": n.status, "scheduled_at": iso(n.scheduled_at), "event_id": n.event_id}


# --- reading --------------------------------------------------------------------------

@bp.get("/notifications")
@permission_required(READ)
def list_notifications():
    tz, stats = local_tz(), _stats()
    stmt = select(Notification).options(selectinload(Notification.audience_role), selectinload(Notification.audience_user),
                                        selectinload(Notification.event), selectinload(Notification.created_by))
    if status := request.args.get("status"):
        stmt = stmt.where(Notification.status == status)
    if kind := request.args.get("type"):
        stmt = stmt.where(Notification.type == kind)
    elif request.args.get("include_reminders") != "yes":
        stmt = stmt.where(Notification.type != "reminder")
    if audience := request.args.get("audience"):
        stmt = stmt.where(Notification.audience == audience)
    if term := search_term():
        stmt = stmt.where(or_(Notification.title.ilike(term, escape="\\"), Notification.body.ilike(term, escape="\\")))
    # Scheduled first (soonest on top), then everything else newest first.
    stmt = stmt.order_by(case((Notification.status == "scheduled", 0), else_=1), Notification.scheduled_at,
                         func.coalesce(Notification.sent_at, Notification.created_at).desc(), Notification.id.desc())
    return jsonify(paginate(stmt, lambda n: notification_json(n, tz, stats)))


@bp.get("/notifications/summary")
@permission_required(READ)
def notifications_summary():
    now = utcnow()
    counts = dict(db.session.execute(
        select(Notification.status, func.count(Notification.id)).where(Notification.type != "reminder")
        .group_by(Notification.status)).all())
    sent_week = db.session.scalar(select(func.count(Notification.id)).where(
        Notification.status == "sent", Notification.sent_at >= now - timedelta(days=7))) or 0
    pending_reminders = db.session.scalar(
        select(func.count(EventReminder.id)).join(Event)
        .where(EventReminder.is_sent.is_(False), Event.status.in_(("upcoming", "ongoing")), Event.start_at > now)) or 0
    return jsonify({
        "draft": counts.get("draft", 0), "scheduled": counts.get("scheduled", 0),
        "sent": counts.get("sent", 0), "cancelled": counts.get("cancelled", 0),
        "sent_last_7_days": sent_week, "pending_reminders": pending_reminders,
        "email_configured": email_configured(), "last_dispatch": last_run(),
    })


@bp.get("/notifications/reminders")
@permission_required(READ)
def upcoming_reminders():
    """Event reminders that will fire in the next N days (default 14)."""
    days = min(max(request.args.get("days", 14, type=int), 1), 60)
    now, tz = utcnow(), local_tz()
    rows = db.session.execute(
        select(EventReminder, Event).join(Event, EventReminder.event_id == Event.id)
        .where(EventReminder.is_sent.is_(False), Event.status.in_(("upcoming", "ongoing")),
               Event.start_at > now, Event.start_at <= now + timedelta(days=days + 31))
        .order_by(Event.start_at)
    ).all()
    items = []
    for reminder, event in rows:
        fires = event.start_at - timedelta(minutes=reminder.offset_minutes)
        if fires > now + timedelta(days=days):
            continue
        items.append({"id": reminder.id, "event": {"id": event.id, "title": event.title},
                      "offset_minutes": reminder.offset_minutes, "channel": reminder.channel,
                      "fires_at": _local(fires, tz), "event_start": _local(event.start_at, tz),
                      "overdue": fires <= now})
    items.sort(key=lambda item: item["fires_at"])
    return jsonify({"items": items, "email_configured": email_configured()})


@bp.get("/notifications/options")
@permission_required(READ)
def notification_options():
    now, tz = utcnow(), local_tz()
    roles = db.session.scalars(select(Role).order_by(Role.name)).all()
    events = db.session.scalars(select(Event).where(Event.start_at >= now - timedelta(days=1))
                                .order_by(Event.start_at).limit(100)).all()
    return jsonify({
        "roles": [{"id": r.id, "name": r.name.replace("_", " ").title(),
                   "members": len(recipient_ids("role", role_id=r.id))} for r in roles],
        "everyone": len(recipient_ids("all")),
        "events": [{"id": e.id, "title": e.title, "start": _local(e.start_at, tz)} for e in events],
    })


@bp.get("/notifications/recipients")
@permission_required(WRITE)
def search_recipients():
    """People to send a direct notification to (minimal fields only)."""
    stmt = select(User).where(User.is_active.is_(True)).order_by(User.full_name).limit(20)
    if term := search_term():
        stmt = stmt.where(or_(User.full_name.ilike(term, escape="\\"), User.email.ilike(term, escape="\\")))
    return jsonify([{"id": u.id, "name": u.full_name, "email": u.email} for u in db.session.scalars(stmt)])


@bp.get("/notifications/<int:notification_id>")
@permission_required(READ)
def get_notification_detail(notification_id):
    return jsonify(notification_json(get_notification(notification_id), local_tz()))


# --- writing --------------------------------------------------------------------------

def _apply(notification, data):
    tz = local_tz()
    if data.audience == "role" and db.session.get(Role, data.audience_role_id) is None:
        raise ApiError("Please correct the highlighted fields.", 422, {"audience_role_id": "This role no longer exists."})
    if data.audience == "user":
        user = db.session.get(User, data.audience_user_id)
        if user is None or not user.is_active:
            raise ApiError("Please correct the highlighted fields.", 422,
                           {"audience_user_id": "Choose an active person."})
    if data.event_id is not None and db.session.get(Event, data.event_id) is None:
        raise ApiError("Please correct the highlighted fields.", 422, {"event_id": "This event no longer exists."})

    scheduled_utc = None
    if data.delivery == "schedule":
        scheduled_utc = to_utc(data.scheduled_local(), tz)
        if scheduled_utc <= utcnow():
            raise ApiError("Please correct the highlighted fields.", 422,
                           {"scheduled_at": "Choose a time in the future, or use “Send now”."})

    notification.title = data.title
    notification.body = data.body
    notification.type = data.type
    notification.audience = data.audience
    notification.audience_role_id = data.audience_role_id if data.audience == "role" else None
    notification.audience_user_id = data.audience_user_id if data.audience == "user" else None
    notification.event_id = data.event_id
    notification.scheduled_at = scheduled_utc
    notification.status = "scheduled" if data.delivery == "schedule" else "draft"


def _finish(notification, data, action, before=None):
    """Log, commit, and send immediately if requested. Returns (json, status_code)."""
    verb = {"now": "sent", "schedule": "scheduled", "draft": "saved as draft"}[data.delivery]
    log_activity(action, notification, summary=f"Notification “{notification.title}” {verb}",
                 before=before, after=snapshot(notification))
    db.session.commit()
    if data.delivery == "now":
        count = deliver(notification)
        db.session.commit()
        if count == 0:
            log_activity("update", notification, summary=f"“{notification.title}” had no recipients")
            db.session.commit()
    return notification_json(notification, local_tz())


@bp.post("/notifications")
@permission_required(WRITE)
def create_notification():
    data = parse_body(NotificationIn)
    notification = Notification(created_by_id=current_user.id)
    _apply(notification, data)
    db.session.add(notification)
    db.session.flush()
    return jsonify(_finish(notification, data, "create")), 201


@bp.put("/notifications/<int:notification_id>")
@permission_required(WRITE)
def update_notification(notification_id):
    notification = get_notification(notification_id)
    if notification.status not in EDITABLE:
        raise ApiError("Sent notifications can't be edited. Duplicate it to send a corrected version.", 409)
    data = parse_body(NotificationIn)
    before = snapshot(notification)
    _apply(notification, data)
    return jsonify(_finish(notification, data, "update", before))


@bp.post("/notifications/<int:notification_id>/send")
@permission_required(WRITE)
def send_notification(notification_id):
    notification = get_notification(notification_id)
    count = deliver(notification, allowed_statuses=("draft", "scheduled"))
    if count is None:
        db.session.rollback()
        raise ApiError("Only drafts and scheduled notifications can be sent.", 409)
    log_activity("update", notification, summary=f"Notification “{notification.title}” sent to {count} people")
    db.session.commit()
    return jsonify(notification_json(notification, local_tz()))


@bp.post("/notifications/<int:notification_id>/cancel")
@permission_required(WRITE)
def cancel_notification(notification_id):
    notification = get_notification(notification_id)
    if notification.status != "scheduled":
        raise ApiError("Only scheduled notifications can be cancelled.", 409)
    notification.status = "cancelled"
    log_activity("update", notification, summary=f"Cancelled scheduled notification “{notification.title}”",
                 before={"status": "scheduled"}, after={"status": "cancelled"})
    db.session.commit()
    return jsonify(notification_json(notification, local_tz()))


@bp.delete("/notifications/<int:notification_id>")
@permission_required(WRITE)
def delete_notification(notification_id):
    notification = get_notification(notification_id)
    removed = db.session.scalar(select(func.count()).select_from(UserNotification)
                                .where(UserNotification.notification_id == notification.id)) or 0
    log_activity("delete", notification,
                 summary=f"Deleted notification “{notification.title}”" + (f" (removed from {removed} inboxes)" if removed else ""),
                 before=snapshot(notification))
    db.session.execute(delete(UserNotification).where(UserNotification.notification_id == notification.id))
    db.session.delete(notification)
    db.session.commit()
    return "", 204


@bp.post("/notifications/dispatch")
@permission_required(WRITE)
def run_dispatch():
    stats = dispatch_due()
    if stats["announcements"] or stats["reminders"] or stats["expired_reminders"]:
        log_activity("update", entity_type="notifications", summary=(
            f"Processed due notifications: {stats['announcements']} scheduled, {stats['reminders']} reminders"))
        db.session.commit()
    return jsonify(stats)
