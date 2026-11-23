"""Answer a chat message. Sources are tried in this order, first fit wins:

1. Configured questions & answers (a clicked suggestion/follow-up, or the best
   match at or above the "Match strictness" threshold)
2. Knowledge base (when "Answer from the knowledge base" is on)
3. AI-generated answer (when AI answers are on and the API key is set)
4. The fallback answer (flagged for review)

Every message and reply is stored, so admins can review conversations.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from ..extensions import db
from ..models import AssistantSettings, ChatMessage, ConfiguredQuestion, KnowledgeEntry
from ..models.base import utcnow
from . import ai_answer, system_settings
from .assistant_config import features_with_defaults
from .classifier import predict_category
from .knowledge_search import rank_knowledge, search_knowledge
from .markdown_render import compose_answer_markdown
from .question_match import best_match, rank_questions

log = logging.getLogger(__name__)

AI_CONTEXT_ENTRIES = 3
AI_NEAR_QUESTION_MIN = 0.3


@dataclass
class Reply:
    content: str                      # Markdown
    resolved_by: str                  # configured | knowledge | ai | fallback
    question: ConfiguredQuestion | None = None
    knowledge: KnowledgeEntry | None = None
    confidence: float | None = None
    needs_review: bool = False
    notes: dict = field(default_factory=dict)


def _question_available(question):
    return (question is not None and question.is_enabled and question.category.is_enabled
            and question.active_answer is not None)


def _configured(question, settings, confidence):
    return Reply(compose_answer_markdown(question.active_answer, settings.assistant_name), "configured",
                 question=question, confidence=confidence)


def _history(session, limit):
    """Earlier turns of this conversation for the AI, oldest first, starting with a user turn."""
    if limit <= 0:
        return []
    rows = db.session.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(limit)).all()
    turns = [{"role": m.role, "content": m.content} for m in reversed(rows)]
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    # The API needs alternating roles; merge any accidental repeats.
    merged = []
    for turn in turns:
        if merged and merged[-1]["role"] == turn["role"]:
            merged[-1]["content"] += "\n\n" + turn["content"]
        else:
            merged.append(dict(turn))
    if merged and merged[-1]["role"] == "user":  # the new message is added after this
        merged.pop()
    return merged


def _try_ai(message, session, settings, features, category):
    knowledge = []
    if features["ai_use_knowledge_context"]:
        knowledge = [entry for _, _, entry in rank_knowledge(message, category)[:AI_CONTEXT_ENTRIES]]
    near = [m.question for m in rank_questions(message, limit=3) if m.score >= AI_NEAR_QUESTION_MIN]
    prompt = ai_answer.build_system_prompt(settings, system_settings.get("app.name"), knowledge, near)
    text = ai_answer.generate_answer(settings, prompt, _history(session, int(features["ai_history_messages"])), message)
    return text


def answer(message, session, question_id=None):
    settings = AssistantSettings.get()
    features = features_with_defaults(settings.features)

    # 1. Configured questions
    if question_id is not None:
        question = db.session.get(ConfiguredQuestion, question_id)
        if _question_available(question):
            return _configured(question, settings, 1.0)
    match, best = best_match(message, settings.match_threshold)
    if match:
        return _configured(match.question, settings, match.score)
    notes = {"best_question_score": best.score if best else None}

    # 2. Knowledge base
    category, _ = predict_category(message)
    if features["use_knowledge_base"]:
        entry = search_knowledge(message, category=category)
        if entry is not None:
            return Reply(entry.content_markdown, "knowledge", knowledge=entry, notes=notes)

    # 3. AI
    if ai_answer.is_ready(settings):
        try:
            text = _try_ai(message, session, settings, features, category)
            if text:
                return Reply(text, "ai", notes=notes)
            notes["ai"] = "no_answer"
        except ai_answer.AIUnavailable as error:
            notes["ai"] = str(error)
        except Exception:  # never let an unexpected AI problem break the chat
            log.exception("Unexpected error while generating an AI answer")
            notes["ai"] = "unexpected error"

    # 4. Fallback
    return Reply(settings.fallback_answer, "fallback", needs_review=True, notes=notes)


def record_exchange(session, text, reply):
    """Store the user's message and the reply; returns (user_message, assistant_message)."""
    now = utcnow()
    user_message = ChatMessage(session_id=session.id, role="user", content=text, created_at=now)
    db.session.add(user_message)
    db.session.flush()
    assistant_message = ChatMessage(
        session_id=session.id, role="assistant", content=reply.content, in_reply_to_id=user_message.id,
        resolved_by=reply.resolved_by, matched_question_id=reply.question.id if reply.question else None,
        matched_knowledge_id=reply.knowledge.id if reply.knowledge else None,
        confidence=reply.confidence, needs_review=reply.needs_review, created_at=utcnow(),
    )
    db.session.add(assistant_message)
    session.last_message_at = assistant_message.created_at
    db.session.flush()
    return user_message, assistant_message
