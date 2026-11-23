"""Audit trail of important actions. Rows are append-only."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import utcnow


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"
    __table_args__ = (
        sa.Index("ix_activity_logs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(sa.String(50))  # create, update, delete, login, login_failed, ...
    entity_type: Mapped[str | None] = mapped_column(sa.String(50))
    entity_id: Mapped[str | None] = mapped_column(sa.String(64))
    summary: Mapped[str | None] = mapped_column(sa.String(255))
    before: Mapped[dict | None] = mapped_column(sa.JSON)
    after: Mapped[dict | None] = mapped_column(sa.JSON)
    ip_address: Mapped[str | None] = mapped_column(sa.String(45))
    user_agent: Mapped[str | None] = mapped_column(sa.String(255))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, default=utcnow, server_default=sa.func.now(), index=True
    )

    actor = relationship("User")
