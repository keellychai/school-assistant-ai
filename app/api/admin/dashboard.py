"""Dashboard overview: key numbers, daily AI Chat usage, how answers were
produced, upcoming events, recent activity and system status.

Days are counted in the school's timezone (System settings), so "today"
matches what admins see on the wall clock.
"""

from collections import Counter
from datetime import timedelta

from flask import current_app, jsonify, request
from flask_login import current_user
from sqlalchemy import func, or_, select, text

from ...auth.security import permission_required
from ...extensions import db
from ...models import (
    ActivityLog,
    AssistantSettings,
    ChatMessage,
    ChatSession,
    ConfiguredQuestion,
    Event,
    KnowledgeEntry,
    Notification,
    User,
)
from ...models.base import utcnow
from ...services.mailer import email_configured
from ...services.notifications import last_run
from ...services.timezone import local_range_utc, local_today, local_tz, to_local
from . import bp
from .assistant import status_json as ai_status
from .routes import _serialize_activity
from .utils import iso

PERIODS = (7, 30, 90)
SOURCES = ("configured", "knowledge", "ai", "fallback")


def _count(stmt):
    return db.session.scalar(stmt) or 0


def _between(column, start, end):
    return (column >= start) & (column < end)


def _period(days):
    tz = local_tz()
    today = local_today(tz)
    first = today - timedelta(days=days - 1)
    start, end = local_range_utc(first, today, tz)
    prev_start, _ = local_range_utc(first - timedelta(days=days), first - timedelta(days=1), tz)
    return tz, first, start, end, prev_start


def _daily(tz, first, days, start, end):
    """[{date, conversations, messages}] for each local day, zeros included."""
    sessions = Counter(to_local(t, tz).date() for t in db.session.scalars(
        select(ChatSession.started_at).where(_between(ChatSession.started_at, start, end))))
    messages = Counter(to_local(t, tz).date() for t in db.session.scalars(
        select(ChatMessage.created_at).where(ChatMessage.role == "user", _between(ChatMessage.created_at, start, end))))
    out = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        out.append({"date": day.isoformat(), "conversations": sessions[day], "messages": messages[day]})
    return out


def _sources(start, end):
    rows = dict(db.session.execute(
        select(ChatMessage.resolved_by, func.count(ChatMessage.id))
        .where(ChatMessage.role == "assistant", ChatMessage.resolved_by.is_not(None),
               _between(ChatMessage.created_at, start, end))
        .group_by(ChatMessage.resolved_by)).all())
    return [{"source": s, "count": rows.get(s, 0)} for s in SOURCES]


def _system_status():
    checks = []
    try:
        db.session.execute(text("SELECT 1"))
        dialect = db.engine.dialect.name
        checks.append({"key": "database", "label": "Database", "state": "ok",
                       "detail": {"postgresql": "PostgreSQL", "sqlite": "SQLite"}.get(dialect, dialect)})
    except Exception:  # pragma: no cover - only when the database is down
        current_app.logger.exception("Dashboard database check failed")
        checks.append({"key": "database", "label": "Database", "state": "error", "detail": "Not reachable"})

    ai = ai_status(AssistantSettings.get())
    checks.append({"key": "ai", "label": "AI answers",
                   "state": {"ready": "ok", "off": "off", "missing_key": "warning"}[ai["state"]],
                   "detail": {"ready": "Ready", "off": "Turned off", "missing_key": "API key not set"}[ai["state"]]})

    run = last_run()
    due = _count(select(func.count(Notification.id)).where(
        Notification.status == "scheduled", Notification.scheduled_at <= utcnow() - timedelta(minutes=5)))
    if due:
        state, detail = "warning", f"{due} overdue: start the worker or open the inbox"
    else:
        state, detail = "ok", "Up to date"
    checks.append({"key": "notifications", "label": "Scheduled notifications", "state": state, "detail": detail,
                   "last_run": run.get("at") if run else None})

    checks.append({"key": "email", "label": "Email delivery", "state": "ok" if email_configured() else "off",
                   "detail": "SMTP configured" if email_configured() else "Not configured (in-app only)"})
    return checks


@bp.get("/dashboard")
@permission_required("dashboard.view")
def dashboard():
    days = request.args.get("days", 30, type=int)
    if days not in PERIODS:
        days = 30
    tz, first, start, end, prev_start = _period(days)
    now = utcnow()

    conversations = _count(select(func.count(ChatSession.id)).where(_between(ChatSession.started_at, start, end)))
    previous = _count(select(func.count(ChatSession.id)).where(_between(ChatSession.started_at, prev_start, start)))
    sources = _sources(start, end)
    answered_total = sum(s["count"] for s in sources)
    fallback = next(s["count"] for s in sources if s["source"] == "fallback")

    totals = {
        "users": _count(select(func.count(User.id)).where(User.is_active.is_(True))),
        "users_inactive": _count(select(func.count(User.id)).where(User.is_active.is_(False))),
        "new_users": _count(select(func.count(User.id)).where(_between(User.created_at, start, end))),
        "conversations": conversations,
        "conversations_previous": previous,
        "questions_asked": _count(select(func.count(ChatMessage.id)).where(
            ChatMessage.role == "user", _between(ChatMessage.created_at, start, end))),
        "answer_rate": round((answered_total - fallback) / answered_total, 3) if answered_total else None,
        "needs_review": _count(select(func.count(ChatMessage.id)).where(
            ChatMessage.needs_review.is_(True), ChatMessage.reviewed_at.is_(None))),
        "upcoming_events": _count(select(func.count(Event.id)).where(
            Event.status == "upcoming", Event.start_at >= now)),
        "events_next_7_days": _count(select(func.count(Event.id)).where(
            Event.status.in_(("upcoming", "ongoing")), Event.start_at >= now, Event.start_at < now + timedelta(days=7))),
        "notifications_sent": _count(select(func.count(Notification.id)).where(
            Notification.status == "sent", _between(Notification.sent_at, start, end))),
        "notifications_scheduled": _count(select(func.count(Notification.id)).where(Notification.status == "scheduled")),
        "knowledge_published": _count(select(func.count(KnowledgeEntry.id)).where(KnowledgeEntry.is_published.is_(True))),
        "questions_enabled": _count(select(func.count(ConfiguredQuestion.id)).where(ConfiguredQuestion.is_enabled.is_(True))),
    }

    upcoming = db.session.scalars(
        select(Event).where(Event.status.in_(("upcoming", "ongoing")), or_(Event.end_at >= now, Event.start_at >= now))
        .order_by(Event.start_at).limit(5)).all()

    activity = None
    if current_user.has_permission("activity.read"):
        activity = [_serialize_activity(log) for log in db.session.scalars(
            select(ActivityLog).order_by(ActivityLog.id.desc()).limit(8))]

    return jsonify({
        "period": {"days": days, "first": first.isoformat(), "last": (first + timedelta(days=days - 1)).isoformat(),
                   "options": list(PERIODS)},
        "totals": totals,
        "daily": _daily(tz, first, days, start, end),
        "sources": sources,
        "upcoming_events": [{"id": e.id, "title": e.title, "start_at": iso(e.start_at), "end_at": iso(e.end_at),
                             "location": e.location, "category": e.category, "status": e.status} for e in upcoming],
        "activity": activity,
        "system": _system_status(),
        "generated_at": iso(now),
    })
