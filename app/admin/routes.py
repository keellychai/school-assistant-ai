import re

from flask import render_template, request
from sqlalchemy import func, select

from ..auth.security import permission_required
from ..extensions import db
from ..models import ActivityLog, ChatSession, Event, SystemSetting, User
from ..models.base import utcnow
from . import bp
from .navigation import ALL_ITEMS, nav_lookup, visible_navigation


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _safe_color(value, default="#4f63df"):
    """The colour is written into a style attribute, so only accept #rrggbb."""
    return value if isinstance(value, str) and _HEX_COLOR.match(value) else default


@bp.context_processor
def inject_layout():
    section, item = nav_lookup(request.endpoint)
    return {
        "admin_nav": visible_navigation(),
        "nav_section": section,
        "nav_item": item,
        "branding": {
            "name": SystemSetting.get_value("app.name", "SchoolSync AI"),
            "logo_url": SystemSetting.get_value("app.logo_url"),
            "primary_color": _safe_color(SystemSetting.get_value("theme.primary_color")),
            "theme": SystemSetting.get_value("theme.mode", "light"),
        },
        "timezone": SystemSetting.get_value("locale.timezone", "Asia/Jakarta"),
    }


def _count(stmt):
    return db.session.scalar(stmt) or 0


@bp.route("/")
@permission_required("dashboard.view")
def index():
    now = utcnow()
    stats = {
        "users": _count(select(func.count(User.id))),
        "conversations": _count(select(func.count(ChatSession.id))),
        "events": _count(select(func.count(Event.id))),
        "upcoming": _count(select(func.count(Event.id)).where(
            Event.status == "upcoming", Event.start_at >= now)),
    }
    upcoming_events = db.session.scalars(
        select(Event).where(Event.status == "upcoming", Event.start_at >= now)
        .order_by(Event.start_at).limit(5)
    ).all()
    recent_activity = db.session.scalars(
        select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(6)
    ).all()
    return render_template("admin/index.html", stats=stats,
                           upcoming_events=upcoming_events, recent_activity=recent_activity)


@bp.route("/activity")
@permission_required("activity.read")
def activity():
    return render_template("admin/activity.html")


def _placeholder_view(item):
    def view():
        return render_template("admin/coming_soon.html")
    view.__name__ = f"placeholder_{item.endpoint}"
    return view


# Modules not built yet get a protected placeholder page so the navigation is complete.
for _section, _item in ALL_ITEMS.values():
    if _item.planned_step is not None:
        bp.add_url_rule(
            _item.path,
            endpoint=_item.endpoint,
            view_func=permission_required(_item.permission)(_placeholder_view(_item)),
        )
