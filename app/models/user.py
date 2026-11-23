"""Users, roles and permissions (RBAC)."""

import secrets
from datetime import datetime

import sqlalchemy as sa
from flask_login import UserMixin
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db, login_manager
from .base import TimestampMixin, utcnow

def _new_session_token():
    return secrets.token_hex(32)


role_permissions = sa.Table(
    "role_permissions",
    db.metadata,
    sa.Column("role_id", sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("permission_id", sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(db.Model):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(sa.String(64), unique=True)
    description: Mapped[str | None] = mapped_column(sa.String(255))

    def __repr__(self):
        return f"<Permission {self.code}>"


class Role(TimestampMixin, db.Model):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(50), unique=True)
    description: Mapped[str | None] = mapped_column(sa.String(255))
    # Built-in roles cannot be deleted or renamed from the admin panel.
    is_system: Mapped[bool] = mapped_column(default=False, server_default=sa.false())

    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions, lazy="selectin")
    users: Mapped[list["User"]] = relationship(back_populates="role")

    def has_permission(self, code):
        return any(p.code == code for p in self.permissions)

    def __repr__(self):
        return f"<Role {self.name}>"


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(sa.String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    full_name: Mapped[str] = mapped_column(sa.String(120))
    avatar_url: Mapped[str | None] = mapped_column(sa.String(500))
    role_id: Mapped[int] = mapped_column(sa.ForeignKey("roles.id", ondelete="RESTRICT"), index=True)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    failed_login_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(sa.DateTime)
    # Stored in the login cookie instead of the numeric id. Rotating it signs the
    # user out of every browser (including "remember me" cookies).
    session_token: Mapped[str] = mapped_column(sa.String(64), unique=True, default=_new_session_token)

    role: Mapped[Role] = relationship(back_populates="users", lazy="joined")

    @validates("email")
    def _normalize_email(self, _key, value):
        return value.strip().lower()

    def get_id(self):
        return self.session_token

    def rotate_session_token(self):
        self.session_token = _new_session_token()

    def set_password(self, password):
        if not password or len(password) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        self.password_hash = generate_password_hash(password)  # scrypt with random salt
        if self.id is not None:  # existing account: end its other sessions
            self.rotate_session_token()

    def check_password(self, password):
        return bool(self.password_hash) and check_password_hash(self.password_hash, password)

    @property
    def is_locked(self):
        return self.locked_until is not None and self.locked_until > utcnow()

    def has_permission(self, code):
        return bool(self.is_active and self.role and self.role.has_permission(code))

    @property
    def can_access_admin(self):
        return self.has_permission("dashboard.view")

    def __repr__(self):
        return f"<User {self.email}>"


@login_manager.user_loader
def load_user(session_token):
    if not session_token:
        return None
    user = db.session.scalar(sa.select(User).where(User.session_token == session_token))
    return user if user and user.is_active else None
