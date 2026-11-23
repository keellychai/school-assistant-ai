"""Shared model helpers."""

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column


def utcnow():
    """Naive UTC timestamp. All datetimes are stored in UTC; convert at display time."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def in_list_check(column, values, name):
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, default=utcnow, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, default=utcnow, onupdate=utcnow, server_default=sa.func.now(), nullable=False
    )
