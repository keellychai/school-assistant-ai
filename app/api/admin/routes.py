from flask import jsonify, request
from flask_login import current_user
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from ...auth.security import permission_required
from ...extensions import db
from ...models import ActivityLog, User
from . import bp
from .utils import iso, paginate, register_error_handlers, search_term

register_error_handlers(bp)


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


# --- Activity log (read-only) -------------------------------------------------

def _serialize_activity(log):
    return {
        "id": log.id,
        "created_at": iso(log.created_at),
        "action": log.action,
        "entity_type": log.entity_type,
        "entity_id": log.entity_id,
        "summary": log.summary,
        "ip_address": log.ip_address,
        "actor": {"id": log.actor.id, "name": log.actor.full_name, "email": log.actor.email} if log.actor else None,
    }


@bp.get("/activity")
@permission_required("activity.read")
def list_activity():
    stmt = select(ActivityLog).options(joinedload(ActivityLog.actor)).order_by(ActivityLog.id.desc())

    if action := request.args.get("action"):
        stmt = stmt.where(ActivityLog.action == action)
    if entity_type := request.args.get("entity_type"):
        stmt = stmt.where(ActivityLog.entity_type == entity_type)
    if term := search_term():
        stmt = stmt.outerjoin(User, ActivityLog.actor_id == User.id).where(or_(
            ActivityLog.summary.ilike(term, escape="\\"),
            ActivityLog.entity_id.ilike(term, escape="\\"),
            ActivityLog.ip_address.ilike(term, escape="\\"),
            User.email.ilike(term, escape="\\"),
            User.full_name.ilike(term, escape="\\"),
        ))

    return jsonify(paginate(stmt, _serialize_activity))


@bp.get("/activity/facets")
@permission_required("activity.read")
def activity_facets():
    """Distinct values for the filter dropdowns."""
    actions = db.session.scalars(select(ActivityLog.action).distinct().order_by(ActivityLog.action)).all()
    entity_types = db.session.scalars(
        select(ActivityLog.entity_type).where(ActivityLog.entity_type.is_not(None))
        .distinct().order_by(ActivityLog.entity_type)
    ).all()
    return jsonify({"actions": actions, "entity_types": entity_types})
