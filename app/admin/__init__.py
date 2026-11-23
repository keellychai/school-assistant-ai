"""Admin Control Panel pages. Every route requires an admin-capable account."""

from flask import Blueprint

from ..auth.security import require_admin_access

bp = Blueprint("admin", __name__, url_prefix="/admin")
bp.before_request(require_admin_access)

from . import routes  # noqa: E402,F401
