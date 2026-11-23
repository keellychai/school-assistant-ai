"""Knowledge base used by the assistant."""

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import TimestampMixin


class KnowledgeCategory(TimestampMixin, db.Model):
    __tablename__ = "knowledge_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(100), unique=True)
    slug: Mapped[str] = mapped_column(sa.String(120), unique=True)
    description: Mapped[str | None] = mapped_column(sa.Text)

    entries: Mapped[list["KnowledgeEntry"]] = relationship(back_populates="category")


class KnowledgeEntry(TimestampMixin, db.Model):
    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        sa.ForeignKey("knowledge_categories.id", ondelete="RESTRICT"), index=True
    )
    external_ref: Mapped[str | None] = mapped_column(sa.String(50), unique=True)  # e.g. "SCH001"
    title: Mapped[str] = mapped_column(sa.String(200))
    content_markdown: Mapped[str] = mapped_column(sa.Text)
    keywords: Mapped[list] = mapped_column(sa.JSON, default=list)
    source_ref: Mapped[str | None] = mapped_column(sa.String(500))
    document_path: Mapped[str | None] = mapped_column(sa.String(500))  # relative to UPLOAD_FOLDER
    document_name: Mapped[str | None] = mapped_column(sa.String(255))
    available_to_ai: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    is_published: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    category: Mapped[KnowledgeCategory] = relationship(back_populates="entries", lazy="joined")
