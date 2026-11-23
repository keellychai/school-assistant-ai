"""Events, schedules and their reminders."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import TimestampMixin, in_list_check

EVENT_STATUSES = ("upcoming", "ongoing", "completed", "cancelled")
REMINDER_CHANNELS = ("in_app", "email")


class Event(TimestampMixin, db.Model):
    __tablename__ = "events"
    __table_args__ = (
        in_list_check("status", EVENT_STATUSES, "valid_status"),
        sa.CheckConstraint("end_at IS NULL OR end_at >= start_at", name="end_after_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(200))
    description: Mapped[str | None] = mapped_column(sa.Text)
    start_at: Mapped[datetime] = mapped_column(sa.DateTime, index=True)
    end_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    all_day: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    location: Mapped[str | None] = mapped_column(sa.String(200))
    category: Mapped[str] = mapped_column(sa.String(50), index=True, default="general")
    status: Mapped[str] = mapped_column(sa.String(20), default="upcoming", server_default="upcoming", index=True)
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    created_by = relationship("User")
    reminders: Mapped[list["EventReminder"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", passive_deletes=True,
        order_by="EventReminder.offset_minutes",
    )

    def __repr__(self):
        return f"<Event {self.title!r} {self.start_at:%Y-%m-%d}>"


class EventReminder(db.Model):
    __tablename__ = "event_reminders"
    __table_args__ = (
        sa.UniqueConstraint("event_id", "offset_minutes", "channel", name="uq_event_reminders_event_offset_channel"),
        sa.CheckConstraint("offset_minutes >= 0", name="offset_non_negative"),
        in_list_check("channel", REMINDER_CHANNELS, "valid_channel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(sa.ForeignKey("events.id", ondelete="CASCADE"), index=True)
    offset_minutes: Mapped[int] = mapped_column(default=60)  # remind N minutes before start
    channel: Mapped[str] = mapped_column(sa.String(20), default="in_app", server_default="in_app")
    is_sent: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime)

    event: Mapped[Event] = relationship(back_populates="reminders")
