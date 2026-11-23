"""User management API: list, profile, create, edit, (de)activate, reset
password, unlock and activity. Guarded against privilege escalation (see
app/services/rbac.py). Users are deactivated, never deleted, so their history
in the activity log stays intact.
"""

import secrets
import string
from datetime import timedelta

from flask import jsonify, request
from flask_login import current_user
from sqlalchemy import func, or_, select

from ...auth.security import permission_required
from ...extensions import db
from ...models import ActivityLog, ChatSession, Role, User, UserNotification
from ...models.base import utcnow
from ...services.audit import log_activity
from ...services.system_settings import password_min_length
from ...services.rbac import (
    ADMIN_ACCESS,
    SUPER_ADMIN,
    can_assign_role,
    can_manage_user,
    is_last_super_admin,
    permission_codes,
)
from . import bp
from .routes import _serialize_activity
from .schemas import PasswordResetIn, UserCreateIn, UserStatusIn, UserUpdateIn
from .utils import ApiError, iso, paginate, parse_body, search_term

READ = "users.read"
WRITE = "users.write"


def _password_min():
    return password_min_length()


def _generate_password():
    alphabet = string.ascii_letters + string.digits
    while True:  # at least one letter and one digit
        value = "".join(secrets.choice(alphabet) for _ in range(14))
        if any(c.isdigit() for c in value) and any(c.isalpha() for c in value):
            return value


def _check_password(value):
    minimum = max(_password_min(), 8)
    if len(value) < minimum:
        raise ApiError("Please correct the highlighted fields.", 422,
                       {"password": f"Use at least {minimum} characters."})


def role_json(role):
    return {"id": role.id, "name": role.name, "label": role.name.replace("_", " ").title(),
            "is_system": role.is_system, "admin_access": ADMIN_ACCESS in permission_codes(role)}


def user_json(user, actor=None):
    actor = actor or current_user
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": role_json(user.role),
        "is_active": user.is_active,
        "is_locked": user.is_locked,
        "locked_until": iso(user.locked_until),
        "failed_login_attempts": user.failed_login_attempts,
        "last_login_at": iso(user.last_login_at),
        "created_at": iso(user.created_at),
        "is_you": user.id == actor.id,
        "can_manage": can_manage_user(actor, user),
    }


def get_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        raise ApiError("User not found.", 404)
    return user


def managed_user(user_id):
    """The user, if the current admin may change them (403 otherwise)."""
    user = get_user(user_id)
    if not can_manage_user(current_user, user):
        raise ApiError("You can't change an account with more access than your own.", 403)
    return user


def _role_for(role_id, field="role_id"):
    role = db.session.get(Role, role_id)
    if role is None:
        raise ApiError("Please correct the highlighted fields.", 422, {field: "Choose a role."})
    if not can_assign_role(current_user, role):
        raise ApiError("Please correct the highlighted fields.", 422,
                       {field: "You can't give a role with more access than your own."})
    return role


def _email_taken(email, exclude_id=None):
    return db.session.scalar(select(User.id).where(User.email == email, User.id != (exclude_id or 0))) is not None


# --- reading -----------------------------------------------------------------------------

@bp.get("/users")
@permission_required(READ)
def list_users():
    stmt = select(User).join(User.role).order_by(User.created_at.desc(), User.id.desc())
    if term := search_term():
        stmt = stmt.where(or_(User.full_name.ilike(term, escape="\\"), User.email.ilike(term, escape="\\")))
    if role := request.args.get("role", type=int):
        stmt = stmt.where(User.role_id == role)
    status = request.args.get("status")
    now = utcnow()
    if status == "active":
        stmt = stmt.where(User.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(User.is_active.is_(False))
    elif status == "locked":
        stmt = stmt.where(User.locked_until > now)
    elif status == "never":
        stmt = stmt.where(User.last_login_at.is_(None))
    if request.args.get("access") == "admin":
        admin_role_ids = [r.id for r in db.session.scalars(select(Role)) if ADMIN_ACCESS in permission_codes(r)]
        stmt = stmt.where(User.role_id.in_(admin_role_ids))
    return jsonify(paginate(stmt, user_json))


@bp.get("/users/summary")
@permission_required(READ)
def users_summary():
    now = utcnow()
    total = db.session.scalar(select(func.count(User.id))) or 0
    active = db.session.scalar(select(func.count(User.id)).where(User.is_active.is_(True))) or 0
    recent = db.session.scalar(select(func.count(User.id)).where(User.last_login_at >= now - timedelta(days=7))) or 0
    locked = db.session.scalar(select(func.count(User.id)).where(User.locked_until > now)) or 0
    roles = db.session.execute(select(Role, func.count(User.id)).outerjoin(User).group_by(Role.id).order_by(Role.name)).all()
    return jsonify({"total": total, "active": active, "inactive": total - active, "signed_in_last_7_days": recent,
                    "locked": locked, "roles": [{**role_json(r), "users": n} for r, n in roles]})


@bp.get("/users/roles")
@permission_required(READ)
def assignable_roles():
    """All roles, marking the ones the current admin may assign."""
    roles = db.session.scalars(select(Role).order_by(Role.name)).all()
    return jsonify([{**role_json(r), "assignable": can_assign_role(current_user, r)} for r in roles])


@bp.get("/users/<int:user_id>")
@permission_required(READ)
def get_user_detail(user_id):
    user = get_user(user_id)
    stats = {
        "chat_sessions": db.session.scalar(select(func.count(ChatSession.id)).where(ChatSession.user_id == user.id)) or 0,
        "unread_notifications": db.session.scalar(select(func.count()).select_from(UserNotification).where(
            UserNotification.user_id == user.id, UserNotification.read_at.is_(None))) or 0,
        "actions": db.session.scalar(select(func.count(ActivityLog.id)).where(ActivityLog.actor_id == user.id)) or 0,
    }
    return jsonify({**user_json(user), "permissions": sorted(permission_codes(user.role)), "stats": stats,
                    "updated_at": iso(user.updated_at)})


@bp.get("/users/<int:user_id>/activity")
@permission_required(READ)
def user_activity(user_id):
    """What this person did, and what was done to their account."""
    user = get_user(user_id)
    stmt = (select(ActivityLog)
            .where(or_(ActivityLog.actor_id == user.id,
                       (ActivityLog.entity_type == "users") & (ActivityLog.entity_id == str(user.id))))
            .order_by(ActivityLog.id.desc()))
    if action := request.args.get("action"):
        stmt = stmt.where(ActivityLog.action == action)
    return jsonify(paginate(stmt, _serialize_activity))


# --- writing -----------------------------------------------------------------------------

@bp.post("/users")
@permission_required(WRITE)
def create_user():
    data = parse_body(UserCreateIn)
    if _email_taken(data.email):
        raise ApiError("Please correct the highlighted fields.", 422, {"email": "An account with this email already exists."})
    role = _role_for(data.role_id)
    generated = data.password is None
    password = _generate_password() if generated else data.password
    if not generated:
        _check_password(password)

    user = User(email=data.email, full_name=data.full_name, role=role, is_active=data.is_active)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    log_activity("create", user, summary=f"Created account {user.email} ({role.name})",
                 after={"email": user.email, "full_name": user.full_name, "role": role.name, "is_active": user.is_active})
    db.session.commit()
    body = user_json(user)
    if generated:
        body["temporary_password"] = password  # shown once; never stored in plain text
    return jsonify(body), 201


@bp.put("/users/<int:user_id>")
@permission_required(WRITE)
def update_user(user_id):
    user = managed_user(user_id)
    data = parse_body(UserUpdateIn)
    if _email_taken(data.email, user.id):
        raise ApiError("Please correct the highlighted fields.", 422, {"email": "An account with this email already exists."})
    before = {"email": user.email, "full_name": user.full_name, "role": user.role.name}

    if data.role_id != user.role_id:
        if user.id == current_user.id:
            raise ApiError("Please correct the highlighted fields.", 422, {"role_id": "You can't change your own role."})
        role = _role_for(data.role_id)
        if is_last_super_admin(user) and role.name != SUPER_ADMIN:
            raise ApiError("Please correct the highlighted fields.", 422,
                           {"role_id": "This is the only active super admin. Make someone else a super admin first."})
        user.role = role

    email_changed = user.email != data.email
    user.full_name, user.email = data.full_name, data.email
    if email_changed and user.id != current_user.id:
        user.rotate_session_token()  # their sign-in identity changed: sign them out everywhere
    after = {"email": user.email, "full_name": user.full_name, "role": user.role.name}
    changed = [k for k in before if before[k] != after[k]]
    if changed:
        log_activity("update", user, summary=f"Updated {user.email}: {', '.join(changed)}",
                     before={k: before[k] for k in changed}, after={k: after[k] for k in changed})
    db.session.commit()
    return jsonify(user_json(user))


@bp.patch("/users/<int:user_id>/status")
@permission_required(WRITE)
def set_user_status(user_id):
    user = managed_user(user_id)
    data = parse_body(UserStatusIn)
    if data.is_active == user.is_active:
        return jsonify(user_json(user))
    if not data.is_active:
        if user.id == current_user.id:
            raise ApiError("You can't deactivate your own account.", 409)
        if is_last_super_admin(user):
            raise ApiError("This is the only active super admin and can't be deactivated.", 409)
    user.is_active = data.is_active
    user.rotate_session_token()  # deactivation signs them out everywhere immediately
    log_activity("update", user, summary=f"{'Activated' if data.is_active else 'Deactivated'} {user.email}",
                 before={"is_active": not data.is_active}, after={"is_active": data.is_active})
    db.session.commit()
    return jsonify(user_json(user))


@bp.post("/users/<int:user_id>/reset-password")
@permission_required(WRITE)
def reset_password(user_id):
    user = managed_user(user_id)
    data = parse_body(PasswordResetIn)
    generated = data.password is None
    password = _generate_password() if generated else data.password
    if not generated:
        _check_password(password)
    user.set_password(password)  # also signs the user out of every session
    user.failed_login_attempts, user.locked_until = 0, None
    log_activity("update", user, summary=f"Reset the password of {user.email}")
    db.session.commit()
    body = user_json(user)
    if generated:
        body["temporary_password"] = password
    return jsonify(body)


@bp.post("/users/<int:user_id>/unlock")
@permission_required(WRITE)
def unlock_user(user_id):
    user = managed_user(user_id)
    was_locked = user.is_locked
    user.failed_login_attempts, user.locked_until = 0, None
    if was_locked:
        log_activity("update", user, summary=f"Unlocked {user.email}")
    db.session.commit()
    return jsonify(user_json(user))
