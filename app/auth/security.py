"""Authorization helpers: permission decorators, unauthorized handling, safe redirects."""

from functools import wraps
from urllib.parse import urlsplit

from flask import abort, jsonify, redirect, request, url_for
from flask_login import current_user

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
