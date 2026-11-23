"""JSON API used by the Admin Control Panel (/api/admin/*).

Every route requires an admin-capable account; individual routes add
@permission_required for their module. Errors are returned as JSON.
"""

from flask import Blueprint

from ...auth.security import require_admin_access

bp = Blueprint("admin_api", __name__, url_prefix="/api/admin")
bp.before_request(require_admin_access)

from . import routes, chat_config, assistant  # noqa: E402,F401
