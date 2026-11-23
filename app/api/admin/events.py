"""Events & schedules API.

Times are exchanged in the school's local time (System Settings timezone) and
stored as UTC. All-day events store local midnight of the first and last day.
Reminders are configured here and delivered by the Notifications module.
"""

from datetime import date, datetime, timedelta

from flask import jsonify, request
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from ...auth.security import permission_required
from ...extensions import db
from ...models import EVENT_STATUSES, Event, EventReminder
from ...models.base import utcnow
from ...services.audit import log_activity
from ...services.timezone import local_range_utc, local_today, local_tz, to_local, to_utc
from . import bp
from .schemas import EventIn, EventStatusIn
from .utils import ApiError, iso, paginate, parse_body, search_term

READ = "events.read"
WRITE = "events.write"
DEFAULT_CATEGORIES = ["general", "exam", "activity", "ceremony", "meeting", "holiday", "trip"]
MAX_CALENDAR_DAYS = 62
FIELDS = ("title", "description", "location", "category", "status", "all_day", "start_at", "end_at")


def _fmt(dt, all_day):
    if dt is None:
        return None
    return dt.date().isoformat() if all_day else dt.strftime("%Y-%m-%dT%H:%M")


def effective_end_utc(event, tz):
    """When the event is over, in UTC (all-day events last until the end of their last local day)."""
    if event.all_day:
        last_day = to_local(event.end_at or event.start_at, tz).date()
        return to_utc(datetime.combine(last_day + timedelta(days=1), datetime.min.time()), tz)
    return event.end_at or event.start_at


def _ended_clause(now):
    # SQL approximation used for filtering; exact all-day handling is done in event_json.
    return func.coalesce(Event.end_at, Event.start_at) < now


def snapshot(event):
    data = {field: getattr(event, field) for field in FIELDS}
    data["start_at"], data["end_at"] = iso(event.start_at), iso(event.end_at)
    data["reminders"] = [f"{r.offset_minutes}m/{r.channel}" for r in event.reminders]
    return data


def event_json(event, tz, now):
    start_local, end_local = to_local(event.start_at, tz), to_local(event.end_at, tz)
    ended = effective_end_utc(event, tz) <= now
    started = event.start_at <= now
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "location": event.location,
        "category": event.category,
        "status": event.status,
        "all_day": event.all_day,
        "start_at": iso(event.start_at),
        "end_at": iso(event.end_at),
        # Local wall-clock values, ready for <input type="datetime-local"> / <input type="date">
        "start": _fmt(start_local, event.all_day),
        "end": _fmt(end_local, event.all_day),
        "start_date": start_local.date().isoformat(),
        "end_date": (end_local or start_local).date().isoformat(),
        "start_time": None if event.all_day else start_local.strftime("%H:%M"),
        "end_time": None if event.all_day or not end_local else end_local.strftime("%H:%M"),
        # Status says upcoming/ongoing but the event is already over (or started): prompt the admin.
        "needs_status_update": (event.status == "upcoming" and started) or (event.status == "ongoing" and ended),
        "is_past": ended,
        "reminders": [
            {
                "id": r.id, "offset_minutes": r.offset_minutes, "channel": r.channel, "is_sent": r.is_sent,
                "fires_at": _fmt(to_local(event.start_at - timedelta(minutes=r.offset_minutes), tz), False),
            }
            for r in event.reminders
        ],
        "created_by": event.created_by.full_name if event.created_by else None,
        "updated_at": iso(event.updated_at),
    }


def get_event(event_id):
    event = db.session.get(Event, event_id)
    if event is None:
        raise ApiError("Event not found.", 404)
    return event


def _parse_date_arg(name):
    value = request.args.get(name)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(f"'{name}' must be a date like 2026-12-01.", 400) from None


# --- list / calendar --------------------------------------------------------------------

@bp.get("/events")
@permission_required(READ)
def list_events():
    tz, now = local_tz(), utcnow()
    stmt = select(Event).options(selectinload(Event.reminders), selectinload(Event.created_by))

    if term := search_term():
        stmt = stmt.where(or_(Event.title.ilike(term, escape="\\"), Event.description.ilike(term, escape="\\"),
                              Event.location.ilike(term, escape="\\")))
    if status := request.args.get("status"):
        stmt = stmt.where(Event.status == status)
    if category := request.args.get("category"):
        stmt = stmt.where(Event.category == category)
    if request.args.get("needs_update") == "yes":
        stmt = stmt.where(or_(
            (Event.status == "upcoming") & (Event.start_at <= now),
            (Event.status == "ongoing") & _ended_clause(now),
        ))

    when = request.args.get("when")
    today = local_today(tz)
    if when == "upcoming":
        stmt = stmt.where(func.coalesce(Event.end_at, Event.start_at) >= now)
    elif when == "past":
        stmt = stmt.where(_ended_clause(now))
    elif when == "today":
        start, end = local_range_utc(today, today, tz)
        stmt = stmt.where(Event.start_at < end, func.coalesce(Event.end_at, Event.start_at) >= start)
    elif when == "week":
        start, end = local_range_utc(today, today + timedelta(days=6), tz)
        stmt = stmt.where(Event.start_at < end, func.coalesce(Event.end_at, Event.start_at) >= start)

    first, last = _parse_date_arg("from"), _parse_date_arg("to")
    if first or last:
        start, end = local_range_utc(first or date(2000, 1, 1), last or date(2100, 12, 31), tz)
        stmt = stmt.where(Event.start_at < end, func.coalesce(Event.end_at, Event.start_at) >= start)

    ascending = when in ("upcoming", "today", "week") or bool(first)
    stmt = stmt.order_by(Event.start_at.asc() if ascending else Event.start_at.desc(), Event.id)
    return jsonify(paginate(stmt, lambda e: event_json(e, tz, now)))


@bp.get("/events/calendar")
@permission_required(READ)
def calendar_events():
    """All events overlapping [start, end] (local dates, inclusive) for the month view."""
    first, last = _parse_date_arg("start"), _parse_date_arg("end")
    if not first or not last or last < first:
        raise ApiError("Give 'start' and 'end' dates, with end on or after start.", 400)
    if (last - first).days > MAX_CALENDAR_DAYS:
        raise ApiError(f"The range can be at most {MAX_CALENDAR_DAYS} days.", 400)
    tz, now = local_tz(), utcnow()
    start, end = local_range_utc(first, last, tz)
    rows = db.session.scalars(
        select(Event).options(selectinload(Event.reminders))
        .where(Event.start_at < end, func.coalesce(Event.end_at, Event.start_at) >= start)
        .order_by(Event.all_day.desc(), Event.start_at, Event.id)
        .limit(1000)
    ).all()
    return jsonify({"items": [event_json(e, tz, now) for e in rows], "today": local_today(tz).isoformat()})


@bp.get("/events/summary")
@permission_required(READ)
def events_summary():
    now = utcnow()
    counts = dict(db.session.execute(select(Event.status, func.count(Event.id)).group_by(Event.status)).all())
    needs_update = db.session.scalar(select(func.count(Event.id)).where(or_(
        (Event.status == "upcoming") & (Event.start_at <= now),
        (Event.status == "ongoing") & _ended_clause(now),
    ))) or 0
    return jsonify({**{s: counts.get(s, 0) for s in EVENT_STATUSES}, "needs_update": needs_update,
                    "total": sum(counts.values())})


@bp.get("/events/categories")
@permission_required(READ)
def event_categories():
    used = db.session.scalars(select(Event.category).distinct()).all()
    return jsonify(sorted(set(DEFAULT_CATEGORIES) | {c for c in used if c}))


@bp.get("/events/<int:event_id>")
@permission_required(READ)
def get_event_detail(event_id):
    return jsonify(event_json(get_event(event_id), local_tz(), utcnow()))


# --- create / update / delete -------------------------------------------------------------

def _apply(event, data):
    tz = local_tz()
    start_utc = to_utc(data.start_local(), tz)
    end_local = data.end_local()
    end_utc = to_utc(end_local, tz) if end_local else None
    time_changed = event.start_at != start_utc

    event.title = data.title
    event.description = data.description
    event.location = data.location
    event.category = data.category
    event.status = data.status
    event.all_day = data.all_day
    event.start_at = start_utc
    event.end_at = end_utc

    # Keep "already sent" for reminders that didn't change, unless the event moved.
    existing = {(r.offset_minutes, r.channel): r for r in event.reminders}
    wanted = [(r.offset_minutes, r.channel) for r in data.reminders]
    for key, reminder in existing.items():
        if key not in wanted:
            event.reminders.remove(reminder)
    for offset, channel in wanted:
        reminder = existing.get((offset, channel))
        if reminder is None:
            event.reminders.append(EventReminder(offset_minutes=offset, channel=channel))
        elif time_changed:
            reminder.is_sent, reminder.sent_at = False, None


@bp.post("/events")
@permission_required(WRITE)
def create_event():
    data = parse_body(EventIn)
    event = Event(created_by_id=current_user.id)
    _apply(event, data)
    db.session.add(event)
    db.session.flush()
    log_activity("create", event, summary=f"Created event “{event.title}”", after=snapshot(event))
    db.session.commit()
    return jsonify(event_json(event, local_tz(), utcnow())), 201


@bp.put("/events/<int:event_id>")
@permission_required(WRITE)
def update_event(event_id):
    event = get_event(event_id)
    data = parse_body(EventIn)
    before = snapshot(event)
    _apply(event, data)
    db.session.flush()
    log_activity("update", event, summary=f"Updated event “{event.title}”", before=before, after=snapshot(event))
    db.session.commit()
    return jsonify(event_json(event, local_tz(), utcnow()))


@bp.patch("/events/<int:event_id>/status")
@permission_required(WRITE)
def set_event_status(event_id):
    event = get_event(event_id)
    data = parse_body(EventStatusIn)
    before = event.status
    event.status = data.status
    log_activity("update", event, summary=f"Marked event “{event.title}” as {data.status}",
                 before={"status": before}, after={"status": data.status})
    db.session.commit()
    return jsonify(event_json(event, local_tz(), utcnow()))


@bp.delete("/events/<int:event_id>")
@permission_required(WRITE)
def delete_event(event_id):
    event = get_event(event_id)
    log_activity("delete", event, summary=f"Deleted event “{event.title}”", before=snapshot(event))
    db.session.delete(event)  # reminders and linked notifications are removed with it
    db.session.commit()
    return "", 204
