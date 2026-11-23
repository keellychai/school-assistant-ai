"""Assistant settings (single row) and general key/value system settings."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .base import TimestampMixin, utcnow

DEFAULT_FALLBACK = (
    "Maaf, saya belum menemukan informasi yang sesuai. "
    "Silakan perjelas pertanyaan atau hubungi admin sekolah."
)


class AssistantSettings(TimestampMixin, db.Model):
    __tablename__ = "assistant_settings"
    __table_args__ = (
        sa.CheckConstraint("id = 1", name="single_row"),
        sa.CheckConstraint("temperature >= 0 AND temperature <= 2", name="temperature_range"),
        sa.CheckConstraint("max_tokens > 0", name="max_tokens_positive"),
        sa.CheckConstraint("match_threshold >= 0 AND match_threshold <= 1", name="threshold_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    assistant_name: Mapped[str] = mapped_column(sa.String(100), default="SchoolSync Assistant")
    avatar_url: Mapped[str | None] = mapped_column(sa.String(500))
    welcome_message: Mapped[str] = mapped_column(
        sa.Text, default="Halo! Saya SchoolSync AI Assistant."
    )
    initial_greeting: Mapped[str] = mapped_column(
        sa.Text,
        default="Tanyakan kepada saya tentang jadwal ujian, pengumuman sekolah, dan prosedur administrasi.",
    )
    default_language: Mapped[str] = mapped_column(sa.String(10), default="id")
    personality: Mapped[str | None] = mapped_column(
        sa.Text, default="Helpful, accurate school information assistant."
    )
    tone: Mapped[str] = mapped_column(sa.String(50), default="friendly")
    fallback_answer: Mapped[str] = mapped_column(sa.Text, default=DEFAULT_FALLBACK)

    # AI provider. The API key itself is NEVER stored here: only the name of the
    # server-side environment variable that holds it.
    ai_enabled: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    ai_provider: Mapped[str | None] = mapped_column(sa.String(50))
    ai_model: Mapped[str | None] = mapped_column(sa.String(100))
    api_key_env_var: Mapped[str | None] = mapped_column(sa.String(100))
    temperature: Mapped[float] = mapped_column(default=0.3, server_default="0.3")
    max_tokens: Mapped[int] = mapped_column(default=512, server_default="512")
    # Minimum similarity for a user message to count as a configured-question match.
    match_threshold: Mapped[float] = mapped_column(default=0.6, server_default="0.6")
    features: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    updated_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    @classmethod
    def get(cls):
        """Return the settings row, creating it with defaults on first use."""
        settings = db.session.get(cls, 1)
        if settings is None:
            settings = cls(id=1)
            db.session.add(settings)
            db.session.commit()
        return settings


class SystemSetting(db.Model):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(sa.String(100), primary_key=True)
    value: Mapped[object] = mapped_column(sa.JSON)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime, default=utcnow, onupdate=utcnow, server_default=sa.func.now()
    )
    updated_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    @classmethod
    def get_value(cls, key, default=None):
        row = db.session.get(cls, key)
        return default if row is None else row.value
