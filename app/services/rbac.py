"""Rules that stop admins from giving themselves (or others) more power than they have.

* You can only manage a user whose role grants nothing beyond your own permissions.
* You can only assign a role whose permissions you hold yourself.
* You can't change your own role or deactivate yourself.
* The last active super admin can't be deactivated or moved to another role,
  and the super_admin role always keeps every permission.
"""

from sqlalchemy import func, select

from ..extensions import db
from ..models import Role, User

SUPER_ADMIN = "super_admin"
ADMIN_ACCESS = "dashboard.view"


def permission_codes(role):
    return {p.code for p in role.permissions} if role else set()


def user_codes(user):
    return permission_codes(user.role) if user and user.is_active else set()


def can_assign_role(actor, role):
    return permission_codes(role) <= user_codes(actor)


def can_manage_user(actor, target):
    return actor.id == target.id or permission_codes(target.role) <= user_codes(actor)


def active_super_admins():
    return db.session.scalar(
        select(func.count(User.id)).join(Role).where(Role.name == SUPER_ADMIN, User.is_active.is_(True))) or 0


def is_last_super_admin(user):
    return user.is_active and user.role.name == SUPER_ADMIN and active_super_admins() <= 1
