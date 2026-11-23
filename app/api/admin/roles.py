"""Roles & permissions API (requires roles.manage).

Built-in roles can't be renamed or deleted. super_admin always keeps every
permission. Changes apply on the users' very next request.
"""

from flask import jsonify
from sqlalchemy import func, select

from ...auth.security import permission_required
from ...extensions import db
from ...models import Permission, Role, User
from ...services.audit import log_activity
from ...services.rbac import ADMIN_ACCESS, SUPER_ADMIN, permission_codes
from . import bp
from .schemas import RoleIn
from .utils import ApiError, iso, parse_body

MANAGE = "roles.manage"

# Display order and labels for the permission matrix.
MODULES = [
    ("dashboard", "Admin panel access", "Required to open the Admin Control Panel at all."),
    ("events", "Events", ""),
    ("notifications", "Notifications", ""),
    ("chatconfig", "AI Chat questions & answers", "Questions, answers, categories, flows and templates."),
    ("assistant", "Assistant settings", ""),
    ("knowledge", "Knowledge base", ""),
    ("monitoring", "Conversations", "Conversation history and analytics."),
    ("users", "Users", ""),
    ("roles", "Roles & permissions", "Create roles and decide what they can do."),
    ("settings", "System settings", ""),
    ("activity", "Activity log", ""),
]
ACTION_LABELS = {"view": "Access", "read": "View", "write": "Edit", "manage": "Manage"}


def catalog():
    by_code = {p.code: p for p in db.session.scalars(select(Permission))}
    groups = []
    for prefix, label, hint in MODULES:
        items = [
            {"code": code, "action": code.split(".", 1)[1], "label": ACTION_LABELS.get(code.split(".", 1)[1], code),
             "description": p.description}
            for code, p in sorted(by_code.items()) if code.split(".", 1)[0] == prefix
        ]
        if items:
            groups.append({"module": prefix, "label": label, "hint": hint, "permissions": items})
    return groups


def role_detail(role, user_count=None):
    codes = sorted(permission_codes(role))
    return {
        "id": role.id, "name": role.name, "label": role.name.replace("_", " ").title(),
        "description": role.description, "is_system": role.is_system, "locked": role.name == SUPER_ADMIN,
        "permissions": codes, "admin_access": ADMIN_ACCESS in codes,
        "users": user_count if user_count is not None else
                 (db.session.scalar(select(func.count(User.id)).where(User.role_id == role.id)) or 0),
        "updated_at": iso(role.updated_at),
    }


def _get_role(role_id):
    role = db.session.get(Role, role_id)
    if role is None:
        raise ApiError("Role not found.", 404)
    return role


def _permissions(codes):
    found = {p.code: p for p in db.session.scalars(select(Permission).where(Permission.code.in_(codes)))}
    unknown = sorted(set(codes) - set(found))
    if unknown:
        raise ApiError("Please correct the highlighted fields.", 422,
                       {"permissions": f"Unknown permission(s): {', '.join(unknown)}"})
    # Write access without read access makes no sense in the UI: include read automatically.
    for code in list(codes):
        if code.endswith(".write"):
            read = code.replace(".write", ".read")
            if read not in found and (p := db.session.scalar(select(Permission).where(Permission.code == read))):
                found[read] = p
    return [found[c] for c in sorted(found)]


def _check_name(name, exclude_id=None):
    if db.session.scalar(select(Role.id).where(Role.name == name, Role.id != (exclude_id or 0))):
        raise ApiError("Please correct the highlighted fields.", 422, {"name": "A role with this name already exists."})


@bp.get("/roles")
@permission_required(MANAGE)
def list_roles():
    counts = dict(db.session.execute(select(User.role_id, func.count(User.id)).group_by(User.role_id)).all())
    roles = db.session.scalars(select(Role).order_by(Role.is_system.desc(), Role.name)).all()
    return jsonify({"roles": [role_detail(r, counts.get(r.id, 0)) for r in roles], "catalog": catalog()})


@bp.post("/roles")
@permission_required(MANAGE)
def create_role():
    data = parse_body(RoleIn)
    _check_name(data.name)
    role = Role(name=data.name, description=data.description, is_system=False)
    role.permissions = _permissions(data.permissions)
    db.session.add(role)
    db.session.flush()
    log_activity("create", role, summary=f"Created role “{role.name}”",
                 after={"name": role.name, "permissions": sorted(permission_codes(role))})
    db.session.commit()
    return jsonify(role_detail(role, 0)), 201


@bp.put("/roles/<int:role_id>")
@permission_required(MANAGE)
def update_role(role_id):
    role = _get_role(role_id)
    data = parse_body(RoleIn)
    if role.name == SUPER_ADMIN:
        if data.name != role.name or set(data.permissions) != {p.code for p in db.session.scalars(select(Permission))}:
            raise ApiError("The super_admin role always has every permission and can't be renamed.", 409)
    if role.is_system and data.name != role.name:
        raise ApiError("Please correct the highlighted fields.", 422, {"name": "Built-in roles can't be renamed."})
    _check_name(data.name, role.id)

    before = {"name": role.name, "description": role.description, "permissions": sorted(permission_codes(role))}
    role.name, role.description = data.name, data.description
    role.permissions = _permissions(data.permissions)
    after = {"name": role.name, "description": role.description, "permissions": sorted(permission_codes(role))}
    added = sorted(set(after["permissions"]) - set(before["permissions"]))
    removed = sorted(set(before["permissions"]) - set(after["permissions"]))
    summary = f"Updated role “{role.name}”"
    if added or removed:
        summary += f" (+{len(added)} / -{len(removed)} permissions)"
    log_activity("update", role, summary=summary, before=before, after=after)
    db.session.commit()
    return jsonify(role_detail(role))


@bp.delete("/roles/<int:role_id>")
@permission_required(MANAGE)
def delete_role(role_id):
    role = _get_role(role_id)
    if role.is_system:
        raise ApiError("Built-in roles can't be deleted.", 409)
    count = db.session.scalar(select(func.count(User.id)).where(User.role_id == role.id)) or 0
    if count:
        raise ApiError(f"{count} user{'s' if count != 1 else ''} still ha{'ve' if count != 1 else 's'} this role. "
                       "Give them another role first.", 409)
    log_activity("delete", role, summary=f"Deleted role “{role.name}”",
                 before={"name": role.name, "permissions": sorted(permission_codes(role))})
    db.session.delete(role)
    db.session.commit()
    return "", 204
