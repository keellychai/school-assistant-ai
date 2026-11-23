"""AI Chat API (/api/chat/*) for any signed-in user.

Everything the chat shows comes from the database: assistant identity and
features (Assistant settings), suggested questions and follow-ups (Questions &
answers), and the user's own conversation history.
"""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from pydantic import Field, StringConstraints
from sqlalchemy import func, select
from typing import Annotated

from ..extensions import db, limiter
from ..models import AssistantSettings, ChatMessage, ChatSession, ConfiguredQuestion, QuestionCategory
from ..services import chat_engine
from ..services.assistant_config import features_with_defaults
from ..services.markdown_render import render_markdown, safe_links
from .admin.schemas import Strict
from .admin.utils import ApiError, iso, parse_body, register_error_handlers

bp = Blueprint("chat_api", __name__, url_prefix="/api/chat")
register_error_handlers(bp)

MAX_MESSAGE_LENGTH = 500
HISTORY_LIMIT = 30


class MessageIn(Strict):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_LENGTH)]
    session_id: Annotated[str, StringConstraints(max_length=36)] | None = None
    question_id: Annotated[int, Field(ge=1)] | None = None  # set when a suggestion/follow-up button was clicked


@bp.before_request
@login_required
def _require_login():
    return None


def _settings():
    settings = AssistantSettings.get()
    return settings, features_with_defaults(settings.features)


def suggested_questions():
    rows = db.session.scalars(
        select(ConfiguredQuestion).join(ConfiguredQuestion.category)
        .where(ConfiguredQuestion.is_suggested.is_(True), ConfiguredQuestion.is_enabled.is_(True),
               QuestionCategory.is_enabled.is_(True))
        .order_by(ConfiguredQuestion.display_order, ConfiguredQuestion.id)).all()
    return [{"id": q.id, "text": q.text} for q in rows if q.active_answer is not None]


def message_json(message, features):
    data = {"id": message.id, "role": message.role, "created_at": iso(message.created_at)}
    if message.role == "user":
        return {**data, "text": message.content}

    question = message.matched_question
    links, follow_ups, source = [], [], None
    if message.resolved_by == "configured" and question is not None:
        if features["show_sources"] and question.active_answer:
            links = safe_links(question.active_answer.links)
        if features["follow_up_buttons"]:
            follow_ups = [{"id": q.id, "text": q.text} for q in question.follow_ups
                          if q.category.is_enabled and q.active_answer is not None]
    elif message.resolved_by == "knowledge" and message.matched_knowledge and features["show_sources"]:
        source = {"type": "knowledge", "title": message.matched_knowledge.title}
    elif message.resolved_by == "ai" and features["show_sources"]:
        source = {"type": "ai", "title": "AI-generated from school information"}

    return {**data, "html": render_markdown(message.content), "resolved_by": message.resolved_by,
            "needs_review": message.needs_review, "links": links, "follow_ups": follow_ups, "source": source}


def _own_session(public_id):
    session = db.session.scalar(select(ChatSession).where(
        ChatSession.public_id == public_id, ChatSession.user_id == current_user.id))
    if session is None:
        raise ApiError("Conversation not found.", 404)
    return session


def _session_title(session_id):
    first = db.session.scalar(select(ChatMessage.content).where(
        ChatMessage.session_id == session_id, ChatMessage.role == "user").order_by(ChatMessage.id).limit(1))
    return (first or "New conversation")[:80]


@bp.get("/config")
def config():
    """What the chat page needs to render itself."""
    settings, features = _settings()
    return jsonify({
        "assistant": {
            "name": settings.assistant_name,
            "avatar_url": settings.avatar_url,
            "welcome_message": settings.welcome_message,
            "initial_greeting": settings.initial_greeting or "",
        },
        "features": {k: features[k] for k in ("suggested_questions", "follow_up_buttons", "show_sources", "chat_history")},
        "suggested": suggested_questions() if features["suggested_questions"] else [],
        "max_length": MAX_MESSAGE_LENGTH,
    })


@bp.get("/sessions")
def list_sessions():
    _, features = _settings()
    if not features["chat_history"]:
        return jsonify({"items": [], "enabled": False})
    counts = (select(ChatMessage.session_id, func.count(ChatMessage.id).label("n"))
              .group_by(ChatMessage.session_id).subquery())
    rows = db.session.execute(
        select(ChatSession, counts.c.n).join(counts, counts.c.session_id == ChatSession.id)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.last_message_at.desc()).limit(HISTORY_LIMIT)).all()
    return jsonify({"enabled": True, "items": [
        {"id": s.public_id, "title": _session_title(s.id), "last_message_at": iso(s.last_message_at), "messages": n}
        for s, n in rows]})


@bp.get("/sessions/<public_id>")
def get_session(public_id):
    _, features = _settings()
    if not features["chat_history"]:
        raise ApiError("Chat history is turned off.", 404)
    session = _own_session(public_id)
    messages = db.session.scalars(select(ChatMessage).where(ChatMessage.session_id == session.id)
                                  .order_by(ChatMessage.created_at, ChatMessage.id)).all()
    return jsonify({"id": session.public_id, "title": _session_title(session.id),
                    "messages": [message_json(m, features) for m in messages]})


@bp.post("/messages")
@limiter.limit("20 per minute")
def send_message():
    data = parse_body(MessageIn)
    _, features = _settings()
    if data.session_id:
        session = _own_session(data.session_id)
    else:
        session = ChatSession(user_id=current_user.id, channel="web")
        db.session.add(session)
        db.session.flush()

    reply = chat_engine.answer(data.message, session, question_id=data.question_id)
    user_message, assistant_message = chat_engine.record_exchange(session, data.message, reply)
    db.session.commit()
    return jsonify({
        "session_id": session.public_id,
        "title": _session_title(session.id),
        "user_message": message_json(user_message, features),
        "reply": message_json(assistant_message, features),
    }), 201
