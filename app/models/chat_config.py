"""AI Chat configuration: categories, questions, answers, follow-ups and flows.

Everything the chat shows (suggested questions, answers, follow-up buttons,
conversation paths) is read from these tables. Nothing is hardcoded.
"""

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import TimestampMixin


class QuestionCategory(TimestampMixin, db.Model):
    __tablename__ = "question_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(100), unique=True)
    slug: Mapped[str] = mapped_column(sa.String(120), unique=True)
    description: Mapped[str | None] = mapped_column(sa.Text)
    display_order: Mapped[int] = mapped_column(default=0, server_default="0")
    is_enabled: Mapped[bool] = mapped_column(default=True, server_default=sa.true())

    questions: Mapped[list["ConfiguredQuestion"]] = relationship(
        back_populates="category", order_by="ConfiguredQuestion.display_order"
    )


class ConfiguredQuestion(TimestampMixin, db.Model):
    __tablename__ = "configured_questions"
    __table_args__ = (
        sa.UniqueConstraint("category_id", "text", name="uq_configured_questions_category_text"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT: a category that still has questions cannot be deleted.
    category_id: Mapped[int] = mapped_column(
        sa.ForeignKey("question_categories.id", ondelete="RESTRICT"), index=True
    )
    text: Mapped[str] = mapped_column(sa.String(500))
    keywords: Mapped[list] = mapped_column(sa.JSON, default=list)
    is_suggested: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    display_order: Mapped[int] = mapped_column(default=0, server_default="0")
    is_enabled: Mapped[bool] = mapped_column(default=True, server_default=sa.true(), index=True)
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    category: Mapped[QuestionCategory] = relationship(back_populates="questions")
    answers: Mapped[list["ConfiguredAnswer"]] = relationship(
        back_populates="question", cascade="all, delete-orphan", passive_deletes=True,
        # Highest priority first; ties go to the oldest answer, so the choice is stable.
        order_by=lambda: [ConfiguredAnswer.priority.desc(), ConfiguredAnswer.id],
    )
    follow_up_links: Mapped[list["FollowUpQuestion"]] = relationship(
        foreign_keys="FollowUpQuestion.parent_question_id",
        back_populates="parent", cascade="all, delete-orphan", passive_deletes=True,
        order_by="FollowUpQuestion.display_order",
    )

    @property
    def active_answer(self):
        """The enabled answer with the highest priority, or None."""
        return next((a for a in self.answers if a.is_enabled), None)

    @property
    def follow_ups(self):
        return [link.child for link in self.follow_up_links if link.child.is_enabled]

    def __repr__(self):
        return f"<ConfiguredQuestion {self.text[:40]!r}>"


class AnswerTemplate(TimestampMixin, db.Model):
    __tablename__ = "answer_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(100), unique=True)
    description: Mapped[str | None] = mapped_column(sa.String(255))
    # Markdown; may contain placeholders such as {assistant_name}.
    body_markdown: Mapped[str] = mapped_column(sa.Text)


class ConfiguredAnswer(TimestampMixin, db.Model):
    __tablename__ = "configured_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        sa.ForeignKey("configured_questions.id", ondelete="CASCADE"), index=True
    )
    body_markdown: Mapped[str] = mapped_column(sa.Text)
    links: Mapped[list] = mapped_column(sa.JSON, default=list)  # [{"label": ..., "url": ...}]
    template_id: Mapped[int | None] = mapped_column(sa.ForeignKey("answer_templates.id", ondelete="SET NULL"))
    priority: Mapped[int] = mapped_column(default=0, server_default="0")
    is_enabled: Mapped[bool] = mapped_column(default=True, server_default=sa.true())

    question: Mapped[ConfiguredQuestion] = relationship(back_populates="answers")
    template = relationship("AnswerTemplate")


class FollowUpQuestion(db.Model):
    """Directed link: after answering `parent`, suggest `child` as a next question."""

    __tablename__ = "follow_up_questions"
    __table_args__ = (
        sa.CheckConstraint("parent_question_id <> child_question_id", name="not_self_referencing"),
    )

    parent_question_id: Mapped[int] = mapped_column(
        sa.ForeignKey("configured_questions.id", ondelete="CASCADE"), primary_key=True
    )
    child_question_id: Mapped[int] = mapped_column(
        sa.ForeignKey("configured_questions.id", ondelete="CASCADE"), primary_key=True
    )
    display_order: Mapped[int] = mapped_column(default=0, server_default="0")

    parent: Mapped[ConfiguredQuestion] = relationship(
        foreign_keys=[parent_question_id], back_populates="follow_up_links"
    )
    child: Mapped[ConfiguredQuestion] = relationship(foreign_keys=[child_question_id])


class ConversationFlow(TimestampMixin, db.Model):
    """A named, predefined conversation path (a tree of questions)."""

    __tablename__ = "conversation_flows"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(120), unique=True)
    description: Mapped[str | None] = mapped_column(sa.Text)
    entry_question_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("configured_questions.id", ondelete="SET NULL")
    )
    is_enabled: Mapped[bool] = mapped_column(default=True, server_default=sa.true())

    entry_question = relationship("ConfiguredQuestion")
    steps: Mapped[list["ConversationFlowStep"]] = relationship(
        back_populates="flow", cascade="all, delete-orphan", passive_deletes=True,
        order_by="ConversationFlowStep.step_order",
    )


class ConversationFlowStep(db.Model):
    __tablename__ = "conversation_flow_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    flow_id: Mapped[int] = mapped_column(sa.ForeignKey("conversation_flows.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(sa.ForeignKey("configured_questions.id", ondelete="CASCADE"))
    parent_step_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("conversation_flow_steps.id", ondelete="CASCADE")
    )
    step_order: Mapped[int] = mapped_column(default=0, server_default="0")

    flow: Mapped[ConversationFlow] = relationship(back_populates="steps")
    question = relationship("ConfiguredQuestion")
    parent = relationship("ConversationFlowStep", remote_side="ConversationFlowStep.id", back_populates="children")
    children = relationship(
        "ConversationFlowStep", back_populates="parent", cascade="all, delete-orphan",
        order_by="ConversationFlowStep.step_order",
    )
