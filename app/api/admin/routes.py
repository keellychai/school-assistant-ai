from flask import jsonify
from flask_login import current_user

from . import bp


@bp.get("/me")
def me():
    """The signed-in admin and their permissions (the frontend uses this to hide controls)."""
    return jsonify({
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "avatar_url": current_user.avatar_url,
        "role": current_user.role.name,
        "permissions": sorted(p.code for p in current_user.role.permissions),
    })
