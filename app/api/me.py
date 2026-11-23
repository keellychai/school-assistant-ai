"""The signed-in user's own inbox (/api/me/notifications). Any logged-in user."""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import func, select, update

from ..extensions import db
from ..models import Notification, UserNotification
from ..models.base import utcnow
from ..services.markdown_render import render_markdown
from ..services.notifications import dispatch_quietly
from .admin.utils import iso

bp = Blueprint("me_api", __name__, url_prefix="/api/me")


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
