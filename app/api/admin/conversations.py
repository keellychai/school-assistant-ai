"""Conversation monitoring: AI Chat history, questions that need review
(answered with the fallback), frequently asked questions and answer analytics.

Reading needs monitoring.read; marking questions reviewed needs monitoring.write.
"""

import re
from collections import Counter
from datetime import date, timedelta

from flask import jsonify, request
from flask_login import current_user
from pydantic import Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import aliased, joinedload

from ...auth.security import permission_required
from ...extensions import db
from ...models import ChatMessage, ChatSession, ConfiguredQuestion, KnowledgeEntry, User
from ...models.base import utcnow
from ...services.audit import log_activity
from ...services.knowledge_search import normalize
from ...services.markdown_render import render_markdown
from ...services.timezone import local_range_utc, local_today, local_tz, to_local
from . import bp
from .schemas import Strict
from .utils import ApiError, iso, paginate, parse_body, search_term

READ = "monitoring.read"
WRITE = "monitoring.write"
SOURCES = ("configured", "knowledge", "ai", "fallback")
PERIODS = (7, 30, 90)


class ReviewIn(Strict):
    ids: list[int] = Field(min_length=1, max_length=200)
    reviewed: bool = True


def _user_json(user):
    return {"id": user.id, "name": user.full_name, "email": user.email} if user else None


def _parse_day(name):
    value = request.args.get(name)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ApiError("Use dates like 2026-11-23.", 422, {name: "Use dates like 2026-11-23."}) from error


def _date_range():
    """Optional ?from=&to= (local days) as a UTC [start, end) range."""
    first, last = _parse_day("from"), _parse_day("to")
    if not first and not last:
        return None, None
    tz = local_tz()
    first = first or date(2000, 1, 1)
    last = last or local_today(tz)
    if last < first:
        raise ApiError("The end date must be on or after the start date.", 422, {"to": "Must be on or after the start date."})
    return local_range_utc(first, last, tz)


# --- Conversations ---------------------------------------------------------------------------------

@bp.get("/conversations")
@permission_required(READ)
def list_conversations():
    asked = func.sum(case((ChatMessage.role == "user", 1), else_=0))
    open_reviews = func.sum(case((and_(ChatMessage.needs_review.is_(True), ChatMessage.reviewed_at.is_(None)), 1), else_=0))
    stats = (select(ChatMessage.session_id, asked.label("questions"), open_reviews.label("open_reviews"),
                    func.sum(case((ChatMessage.resolved_by == "ai", 1), else_=0)).label("ai"))
             .group_by(ChatMessage.session_id).subquery())
    stmt = (select(ChatSession, stats.c.questions, stats.c.open_reviews, stats.c.ai)
            .join(stats, stats.c.session_id == ChatSession.id)
            .options(joinedload(ChatSession.user))
            .order_by(ChatSession.last_message_at.desc(), ChatSession.id.desc()))

    if request.args.get("status") == "needs_review":
        stmt = stmt.where(stats.c.open_reviews > 0)
    if source := request.args.get("source"):
        if source not in SOURCES:
            raise ApiError("Unknown answer source.", 422, {"source": "Unknown answer source."})
        stmt = stmt.where(ChatSession.id.in_(select(ChatMessage.session_id).where(ChatMessage.resolved_by == source)))
    if user_id := request.args.get("user_id", type=int):
        stmt = stmt.where(ChatSession.user_id == user_id)
    start, end = _date_range()
    if start:
        stmt = stmt.where(ChatSession.last_message_at >= start, ChatSession.started_at < end)
    if term := search_term():
        matching = select(ChatMessage.session_id).where(ChatMessage.content.ilike(term, escape="\\"))
        stmt = stmt.outerjoin(User, ChatSession.user_id == User.id).where(or_(
            ChatSession.id.in_(matching), User.full_name.ilike(term, escape="\\"), User.email.ilike(term, escape="\\")))

    page = paginate(stmt, tuple, rows=True)
    sessions = [row[0] for row in page["items"]]
    firsts = _first_questions([s.id for s in sessions])
    page["items"] = [{
        "id": s.public_id,
        "user": _user_json(s.user),
        "title": firsts.get(s.id, "—"),
        "started_at": iso(s.started_at),
        "last_message_at": iso(s.last_message_at),
        "questions": int(questions or 0),
        "open_reviews": int(open_count or 0),
        "ai_answers": int(ai or 0),
    } for s, questions, open_count, ai in page["items"]]
    return jsonify(page)


def _first_questions(session_ids):
    if not session_ids:
        return {}
    first_ids = (select(func.min(ChatMessage.id)).where(ChatMessage.session_id.in_(session_ids), ChatMessage.role == "user")
                 .group_by(ChatMessage.session_id))
    rows = db.session.execute(select(ChatMessage.session_id, ChatMessage.content).where(ChatMessage.id.in_(first_ids))).all()
    return {sid: text[:120] for sid, text in rows}


def _message_json(message):
    data = {"id": message.id, "role": message.role, "created_at": iso(message.created_at)}
    if message.role == "user":
        return {**data, "text": message.content}
    question, entry = message.matched_question, message.matched_knowledge
    return {
        **data,
        "html": render_markdown(message.content),
        "resolved_by": message.resolved_by,
        "confidence": message.confidence,
        "needs_review": message.needs_review,
        "reviewed_at": iso(message.reviewed_at),
        "question": {"id": question.id, "text": question.text} if question else None,
        "knowledge": {"id": entry.id, "title": entry.title} if entry else None,
    }


@bp.get("/conversations/<public_id>")
@permission_required(READ)
def get_conversation(public_id):
    session = db.session.scalar(select(ChatSession).where(ChatSession.public_id == public_id)
                                .options(joinedload(ChatSession.user)))
    if session is None:
        raise ApiError("Conversation not found.", 404)
    messages = db.session.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session.id)
        .options(joinedload(ChatMessage.matched_question), joinedload(ChatMessage.matched_knowledge))
        .order_by(ChatMessage.created_at, ChatMessage.id)).all()
    return jsonify({
        "id": session.public_id,
        "user": _user_json(session.user),
        "channel": session.channel,
        "started_at": iso(session.started_at),
        "last_message_at": iso(session.last_message_at),
        "messages": [_message_json(m) for m in messages],
    })


# --- Needs review ---------------------------------------------------------------------------------------

@bp.get("/conversations/review")
@permission_required(READ)
def review_queue():
    """Replies that used the fallback (or were flagged), with the question that was asked."""
    Asked = aliased(ChatMessage)
    stmt = (select(ChatMessage, Asked.content, ChatSession)
            .join(Asked, ChatMessage.in_reply_to_id == Asked.id)
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .options(joinedload(ChatSession.user))
            .where(ChatMessage.needs_review.is_(True))
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()))
    status = request.args.get("status", "open")
    if status == "open":
        stmt = stmt.where(ChatMessage.reviewed_at.is_(None))
    elif status == "reviewed":
        stmt = stmt.where(ChatMessage.reviewed_at.is_not(None))
    if term := search_term():
        stmt = stmt.where(Asked.content.ilike(term, escape="\\"))
    start, end = _date_range()
    if start:
        stmt = stmt.where(ChatMessage.created_at >= start, ChatMessage.created_at < end)

    page = paginate(stmt, tuple, rows=True)
    page["items"] = [{
        "id": reply.id,
        "question": asked,
        "resolved_by": reply.resolved_by,
        "created_at": iso(reply.created_at),
        "reviewed_at": iso(reply.reviewed_at),
        "conversation_id": session.public_id,
        "user": _user_json(session.user),
    } for reply, asked, session in page["items"]]
    page["open_total"] = db.session.scalar(select(func.count(ChatMessage.id)).where(
        ChatMessage.needs_review.is_(True), ChatMessage.reviewed_at.is_(None))) or 0
    return jsonify(page)


@bp.post("/conversations/review")
@permission_required(READ, WRITE)
def mark_reviewed():
    data = parse_body(ReviewIn)
    messages = db.session.scalars(select(ChatMessage).where(
        ChatMessage.id.in_(set(data.ids)), ChatMessage.needs_review.is_(True))).all()
    if not messages:
        raise ApiError("None of these questions need review.", 404)
    now = utcnow()
    changed = [m for m in messages if bool(m.reviewed_at) != data.reviewed]
    for message in changed:
        message.reviewed_at = now if data.reviewed else None
    if changed:
        verb = "reviewed" if data.reviewed else "reopened"
        log_activity("update", entity_type="chat_messages", entity_id=",".join(str(m.id) for m in changed)[:100],
                     summary=f"Marked {len(changed)} unanswered question{'s' if len(changed) != 1 else ''} as {verb}",
                     after={"ids": [m.id for m in changed], "reviewed": data.reviewed})
    db.session.commit()
    return jsonify({"updated": len(changed)})


# --- Analytics --------------------------------------------------------------------------------------------

def _group_texts(texts, limit):
    """Most common questions, grouping wording that only differs in case/punctuation."""
    counts, examples = Counter(), {}
    for text in texts:
        key = normalize(text)
        if not key:
            continue
        counts[key] += 1
        examples.setdefault(key, re.sub(r"\s+", " ", text).strip()[:200])
    return [{"text": examples[key], "count": n} for key, n in counts.most_common(limit)]


@bp.get("/conversations/stats")
@permission_required(READ)
def conversation_stats():
    days = request.args.get("days", 30, type=int)
    days = days if days in PERIODS else 30
    tz = local_tz()
    today = local_today(tz)
    first = today - timedelta(days=days - 1)
    start, end = local_range_utc(first, today, tz)
    in_period = and_(ChatMessage.created_at >= start, ChatMessage.created_at < end)

    by_source = dict(db.session.execute(
        select(ChatMessage.resolved_by, func.count(ChatMessage.id))
        .where(ChatMessage.role == "assistant", in_period).group_by(ChatMessage.resolved_by)).all())
    answered = sum(by_source.get(s, 0) for s in SOURCES)

    # Daily replies per source (local days)
    daily = {first + timedelta(days=i): Counter() for i in range(days)}
    for created, source in db.session.execute(select(ChatMessage.created_at, ChatMessage.resolved_by)
                                              .where(ChatMessage.role == "assistant", in_period)):
        day = to_local(created, tz).date()
        if day in daily and source:
            daily[day][source] += 1

    top_configured = db.session.execute(
        select(ConfiguredQuestion.id, ConfiguredQuestion.text, func.count(ChatMessage.id).label("n"))
        .join(ChatMessage, ChatMessage.matched_question_id == ConfiguredQuestion.id)
        .where(ChatMessage.resolved_by == "configured", in_period)
        .group_by(ConfiguredQuestion.id, ConfiguredQuestion.text).order_by(func.count(ChatMessage.id).desc()).limit(10)).all()
    top_knowledge = db.session.execute(
        select(KnowledgeEntry.id, KnowledgeEntry.title, func.count(ChatMessage.id))
        .join(ChatMessage, ChatMessage.matched_knowledge_id == KnowledgeEntry.id)
        .where(ChatMessage.resolved_by == "knowledge", in_period)
        .group_by(KnowledgeEntry.id, KnowledgeEntry.title).order_by(func.count(ChatMessage.id).desc()).limit(10)).all()

    Asked = aliased(ChatMessage)
    asked_texts = db.session.scalars(select(ChatMessage.content).where(ChatMessage.role == "user", in_period)).all()
    unanswered_texts = db.session.scalars(
        select(Asked.content).join(ChatMessage, ChatMessage.in_reply_to_id == Asked.id)
        .where(ChatMessage.resolved_by == "fallback", in_period)).all()

    return jsonify({
        "period": {"days": days, "first": first.isoformat(), "last": today.isoformat()},
        "totals": {
            "conversations": db.session.scalar(select(func.count(func.distinct(ChatMessage.session_id))).where(in_period)) or 0,
            "questions": len(asked_texts),
            "people": db.session.scalar(select(func.count(func.distinct(ChatSession.user_id)))
                                        .join(ChatMessage, ChatMessage.session_id == ChatSession.id).where(in_period)) or 0,
            "answered_rate": round((answered - by_source.get("fallback", 0)) / answered, 3) if answered else None,
            "open_reviews": db.session.scalar(select(func.count(ChatMessage.id)).where(
                ChatMessage.needs_review.is_(True), ChatMessage.reviewed_at.is_(None))) or 0,
        },
        "sources": [{"source": s, "count": by_source.get(s, 0)} for s in SOURCES],
        "daily": [{"date": d.isoformat(), **{s: c[s] for s in SOURCES}} for d, c in daily.items()],
        "frequent_questions": _group_texts(asked_texts, 10),
        "frequent_unanswered": _group_texts(unanswered_texts, 10),
        "top_configured": [{"id": i, "text": t, "count": n} for i, t, n in top_configured],
        "top_knowledge": [{"id": i, "title": t, "count": n} for i, t, n in top_knowledge],
    })
