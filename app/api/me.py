"""The signed-in user's own account (/api/me/*): inbox, profile and password. Any logged-in user."""

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required, login_user
from sqlalchemy import func, select, update

from ..extensions import db, limiter
from ..models import ActivityLog, ChatSession, Event, Notification, User, UserNotification
from ..models.base import utcnow
from ..services.audit import log_activity
from ..services.markdown_render import render_markdown
from ..services.notifications import dispatch_quietly
from ..services.system_settings import password_min_length
from ..services.timezone import to_local
from .admin.schemas import PasswordChangeIn, ProfileIn
from .admin.utils import ApiError, iso, parse_body, register_error_handlers

bp = Blueprint("me_api", __name__, url_prefix="/api/me")
register_error_handlers(bp)


@bp.before_request
@login_required
def _require_login():
    return None


def _unread_count():
    return db.session.scalar(select(func.count()).select_from(UserNotification).where(
        UserNotification.user_id == current_user.id, UserNotification.read_at.is_(None))) or 0


@bp.get("/notifications")
def my_notifications():
    dispatch_quietly()  # deliver anything that has come due, even without a background worker
    limit = min(max(request.args.get("limit", 20, type=int), 1), 50)
    rows = db.session.execute(
        select(UserNotification, Notification)
        .join(Notification, UserNotification.notification_id == Notification.id)
        .where(UserNotification.user_id == current_user.id)
        .order_by(UserNotification.delivered_at.desc(), Notification.id.desc())
        .limit(limit)
    ).all()
    return jsonify({
        "unread": _unread_count(),
        "items": [{
            "id": n.id, "title": n.title, "body_html": render_markdown(n.body), "type": n.type,
            "event_id": n.event_id, "delivered_at": iso(un.delivered_at), "read": un.read_at is not None,
        } for un, n in rows],
    })


@bp.post("/notifications/<int:notification_id>/read")
def mark_read(notification_id):
    db.session.execute(update(UserNotification).where(
        UserNotification.user_id == current_user.id, UserNotification.notification_id == notification_id,
        UserNotification.read_at.is_(None)).values(read_at=utcnow()))
    db.session.commit()
    return jsonify({"unread": _unread_count()})


@bp.post("/notifications/read-all")
def mark_all_read():
    db.session.execute(update(UserNotification).where(
        UserNotification.user_id == current_user.id, UserNotification.read_at.is_(None)).values(read_at=utcnow()))
    db.session.commit()
    return jsonify({"unread": 0})


# --- School events (read-only, for the student dashboard) -------------------------------

@bp.get("/events")
def upcoming_events():
    """Upcoming and ongoing events in the next N days (default 30), soonest first."""
    from datetime import timedelta
    from sqlalchemy import or_
    days = min(max(request.args.get("days", 30, type=int), 1), 90)
    now = utcnow()
    events = db.session.scalars(
        select(Event).where(Event.status.in_(("upcoming", "ongoing")),
                            or_(Event.end_at >= now, Event.start_at >= now), Event.start_at < now + timedelta(days=days))
        .order_by(Event.start_at).limit(20)).all()
    return jsonify({"items": [{
        "id": e.id, "title": e.title, "description": e.description, "location": e.location,
        "category": e.category, "status": e.status, "all_day": e.all_day,
        "start_at": iso(e.start_at), "end_at": iso(e.end_at),
        "start_local": to_local(e.start_at).isoformat(timespec="minutes"),
    } for e in events]})


# --- Profile & password -------------------------------------------------------------

def _profile_json(user):
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": {"name": user.role.name, "label": user.role.name.replace("_", " ").title()},
        "last_login_at": iso(user.last_login_at),
        "created_at": iso(user.created_at),
        "password_min_length": password_min_length(),
        "stats": {
            "chat_sessions": db.session.scalar(select(func.count(ChatSession.id)).where(ChatSession.user_id == user.id)) or 0,
            "actions": db.session.scalar(select(func.count(ActivityLog.id)).where(ActivityLog.actor_id == user.id)) or 0,
        },
    }


def _stay_signed_in(user):
    """Credentials changed, so the session token was rotated (signing out every other
    device). Re-issue the login for this browser so the user isn't signed out here too."""
    remembered = bool(request.cookies.get(current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token")))
    login_user(user, remember=remembered)


def _require_current_password(user, password):
    if not password or not user.check_password(password):
        log_activity("password_check_failed", user, summary="Wrong current password on My profile")
        db.session.commit()
        raise ApiError("Please correct the highlighted fields.", 422,
                       {"current_password": "Your current password is incorrect."})


@bp.get("/profile")
def get_profile():
    return jsonify(_profile_json(current_user))


@bp.put("/profile")
@limiter.limit("10 per minute")
def update_profile():
    data = parse_body(ProfileIn)
    user = current_user._get_current_object()
    email_changed = data.email != user.email
    if email_changed:
        _require_current_password(user, data.current_password)
        if db.session.scalar(select(User.id).where(User.email == data.email, User.id != user.id)):
            raise ApiError("Please correct the highlighted fields.", 422,
                           {"email": "Another account already uses this email."})

    before = {"full_name": user.full_name, "email": user.email}
    user.full_name, user.email = data.full_name, data.email
    after = {"full_name": user.full_name, "email": user.email}
    changed = sorted(k for k in before if before[k] != after[k])
    if changed:
        if email_changed:
            user.rotate_session_token()
        log_activity("update", user, summary="Updated own profile: " + ", ".join(changed).replace("_", " "),
                     before={k: before[k] for k in changed}, after={k: after[k] for k in changed})
    db.session.commit()
    if email_changed:
        _stay_signed_in(user)
    return jsonify({**_profile_json(user), "changed": changed})


@bp.put("/password")
@limiter.limit("5 per minute")
def change_password():
    data = parse_body(PasswordChangeIn)
    user = current_user._get_current_object()
    _require_current_password(user, data.current_password)
    minimum = password_min_length()
    if len(data.new_password) < minimum:
        raise ApiError("Please correct the highlighted fields.", 422,
                       {"new_password": f"Use at least {minimum} characters."})
    user.set_password(data.new_password)  # also rotates the session token
    log_activity("password_changed", user, summary="Changed own password (other sessions signed out)")
    db.session.commit()
    _stay_signed_in(user)
    return jsonify({"ok": True})
