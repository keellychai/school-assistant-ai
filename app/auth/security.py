"""Authorization helpers: permission decorators, unauthorized handling, safe redirects."""

import time
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user, logout_user

from ..extensions import db, login_manager
from ..services.audit import log_activity


def is_api_request():
    return request.path.startswith("/api/")


def home_url_for(user):
    """Where a user lands after signing in."""
    return url_for("admin.index") if user.can_access_admin else url_for("main.dashboard")


def is_safe_next(target):
    """Only allow redirects to a path on this site (blocks //evil.com, https://evil.com, \\evil)."""
    if not target or "\\" in target:
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//")


@login_manager.unauthorized_handler
def _unauthorized():
    if is_api_request():
        return jsonify({"error": "Authentication required."}), 401
    next_path = request.full_path.rstrip("?") if request.method == "GET" else None
    return redirect(url_for("auth.login", next=next_path) if next_path else url_for("auth.login"))


def _deny(codes):
    log_activity(
        "permission_denied",
        entity_type="route",
        entity_id=request.endpoint,
        summary=f"{request.method} {request.path} requires {', '.join(codes)}",
    )
    db.session.commit()
    abort(403)


def permission_required(*codes, any_of=False):
    """Require login plus permission codes (all of them, or any one with any_of=True).

    Unauthenticated: redirect to login (pages) or 401 JSON (API).
    Missing permission: 403, and the attempt is written to the activity log.
    """
    if not codes:
        raise ValueError("permission_required needs at least one permission code")

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            check = any if any_of else all
            if not check(current_user.has_permission(code) for code in codes):
                _deny(codes)
            return view(*args, **kwargs)

        wrapped.required_permissions = codes
        return wrapped

    return decorator


def require_admin_access():
    """`before_request` hook for admin blueprints: every route needs an admin-capable account."""
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    if not current_user.can_access_admin:
        _deny(("dashboard.view",))
    return None


# --- Idle session timeout ----------------------------------------------------------

LAST_SEEN_KEY = "_last_seen"
_TOUCH_EVERY_SECONDS = 30  # don't rewrite the session cookie on every single request


def enforce_session_timeout():
    """App-wide `before_request`: sign out sessions idle for longer than the
    "Session timeout" system setting. Background polls (X-Background-Request)
    are allowed through but don't count as activity."""
    if request.endpoint in (None, "static") or not current_user.is_authenticated:
        return None
    from ..services.system_settings import session_timeout_minutes

    now = int(time.time())
    minutes = session_timeout_minutes()
    last_seen = session.get(LAST_SEEN_KEY)
    if isinstance(last_seen, int) and now - last_seen > minutes * 60:
        log_activity("session_expired", current_user, summary=f"Signed out after {minutes} minutes without activity")
        db.session.commit()
        logout_user()
        session.clear()
        message = f"You were signed out after {minutes} minutes without activity. Please sign in again."
        if is_api_request():
            return jsonify({"error": message}), 401
        flash(message, "info")
        next_path = request.full_path.rstrip("?") if request.method == "GET" else None
        return redirect(url_for("auth.login", next=next_path) if is_safe_next(next_path) else url_for("auth.login"))

    background = request.headers.get("X-Background-Request") == "1"
    if not background and (not isinstance(last_seen, int) or now - last_seen >= _TOUCH_EVERY_SECONDS):
        session[LAST_SEEN_KEY] = now
    elif last_seen is None:
        session[LAST_SEEN_KEY] = now
    return None
