"""Chat sessions and messages (history + monitoring data)."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import in_list_check, utcnow

MESSAGE_ROLES = ("user", "assistant")
RESOLVED_BY = ("configured", "knowledge", "ai", "fallback")


class ChatSession(db.Model):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Random public id so the browser never sees sequential database ids.
    public_id: Mapped[str] = mapped_column(sa.String(36), unique=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    channel: Mapped[str] = mapped_column(sa.String(20), default="web", server_default="web")
    started_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow, server_default=sa.func.now())
    last_message_at: Mapped[datetime | None] = mapped_column(sa.DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(sa.DateTime)

    user = relationship("User")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True,
        order_by="ChatMessage.created_at",
    )


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"
    __table_args__ = (
        in_list_check("role", MESSAGE_ROLES, "valid_role"),
        sa.CheckConstraint(
            "resolved_by IS NULL OR resolved_by IN ('configured', 'knowledge', 'ai', 'fallback')",
            name="valid_resolved_by",
        ),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
        sa.Index("ix_chat_messages_resolved_by_created_at", "resolved_by", "created_at"),
        sa.Index("ix_chat_messages_needs_review_created_at", "needs_review", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(sa.String(10))
    content: Mapped[str] = mapped_column(sa.Text)
    # For assistant messages: the user message being answered.
    in_reply_to_id: Mapped[int | None] = mapped_column(sa.ForeignKey("chat_messages.id", ondelete="SET NULL"))
    resolved_by: Mapped[str | None] = mapped_column(sa.String(20))
    matched_question_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("configured_questions.id", ondelete="SET NULL"), index=True
    )
    matched_knowledge_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("knowledge_entries.id", ondelete="SET NULL")
    )
    confidence: Mapped[float | None] = mapped_column()
    needs_review: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow, server_default=sa.func.now())

    session: Mapped[ChatSession] = relationship(back_populates="messages")
    in_reply_to = relationship("ChatMessage", remote_side="ChatMessage.id")
    matched_question = relationship("ConfiguredQuestion")
    matched_knowledge = relationship("KnowledgeEntry")
