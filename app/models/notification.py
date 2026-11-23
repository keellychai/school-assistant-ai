"""Notifications, announcements and per-user delivery records."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import TimestampMixin, in_list_check

NOTIFICATION_TYPES = ("announcement", "reminder", "system")
NOTIFICATION_AUDIENCES = ("all", "role", "user")
NOTIFICATION_STATUSES = ("draft", "scheduled", "sent", "cancelled")


class Notification(TimestampMixin, db.Model):
    __tablename__ = "notifications"
    __table_args__ = (
        in_list_check("type", NOTIFICATION_TYPES, "valid_type"),
        in_list_check("audience", NOTIFICATION_AUDIENCES, "valid_audience"),
        in_list_check("status", NOTIFICATION_STATUSES, "valid_status"),
        sa.CheckConstraint("audience <> 'role' OR audience_role_id IS NOT NULL", name="role_audience_has_role"),
        sa.CheckConstraint("audience <> 'user' OR audience_user_id IS NOT NULL", name="user_audience_has_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(200))
    body: Mapped[str] = mapped_column(sa.Text)
    type: Mapped[str] = mapped_column(sa.String(20), default="announcement", server_default="announcement")
    audience: Mapped[str] = mapped_column(sa.String(10), default="all", server_default="all")
    audience_role_id: Mapped[int | None] = mapped_column(sa.ForeignKey("roles.id", ondelete="SET NULL"))
    audience_user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(sa.String(20), default="draft", server_default="draft", index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(sa.DateTime, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    event_id: Mapped[int | None] = mapped_column(sa.ForeignKey("events.id", ondelete="CASCADE"), index=True)
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    audience_role = relationship("Role")
    audience_user = relationship("User", foreign_keys=[audience_user_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    event = relationship("Event")
    deliveries: Mapped[list["UserNotification"]] = relationship(
        back_populates="notification", cascade="all, delete-orphan", passive_deletes=True
    )


class UserNotification(db.Model):
    __tablename__ = "user_notifications"

    notification_id: Mapped[int] = mapped_column(
        sa.ForeignKey("notifications.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    read_at: Mapped[datetime | None] = mapped_column(sa.DateTime)

    notification: Mapped[Notification] = relationship(back_populates="deliveries")
    user = relationship("User")
