"""AI Chat configuration API: categories, questions, answers, follow-ups,
answer templates, conversation flows and the fallback answer.

Reads need `chatconfig.read`; every change needs `chatconfig.write` and is
written to the activity log with before/after values.
"""

import re
import unicodedata

import sqlalchemy as sa
from flask import jsonify, request
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ...auth.security import permission_required
from ...extensions import db
from ...models import (
    AnswerTemplate,
    AssistantSettings,
    ConfiguredAnswer,
    ConfiguredQuestion,
    ConversationFlow,
    ConversationFlowStep,
    FollowUpQuestion,
    QuestionCategory,
)
from ...services.audit import log_activity
from ...services.markdown_render import compose_answer_markdown, render_markdown, safe_links
from . import bp
from .schemas import (
    AnswerIn,
    AnswerPatch,
    CategoryIn,
    FallbackIn,
    FlowIn,
    FollowUpsIn,
    PreviewIn,
    QuestionCreate,
    QuestionIn,
    QuestionPatch,
    ReorderIn,
    TemplateIn,
)
from .utils import ApiError, iso, paginate, parse_body, search_term

READ = "chatconfig.read"
WRITE = "chatconfig.write"


# --- helpers ----------------------------------------------------------------------

def get_or_404(model, object_id, label):
    obj = db.session.get(model, object_id)
    if obj is None:
        raise ApiError(f"{label} not found.", 404)
    return obj


def snapshot(obj, *fields):
    def plain(value):
        return value if isinstance(value, (str, int, float, bool, list, dict, type(None))) else str(value)
    return {field: plain(getattr(obj, field)) for field in fields}


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:100] or "category"


def unique_slug(base, exclude_id=None):
    slug, n = base, 2
    while True:
        stmt = select(QuestionCategory.id).where(QuestionCategory.slug == slug)
        if exclude_id:
            stmt = stmt.where(QuestionCategory.id != exclude_id)
        if db.session.scalar(stmt) is None:
            return slug
        slug, n = f"{base}-{n}", n + 1


def commit_or_conflict(field, message):
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise ApiError("This conflicts with an existing record.", 409, {field: message}) from error


def assistant_name():
    return AssistantSettings.get().assistant_name


# --- serializers ------------------------------------------------------------------

def category_json(category, question_count=None):
    return {
        "id": category.id,
        "name": category.name,
        "slug": category.slug,
        "description": category.description,
        "display_order": category.display_order,
        "is_enabled": category.is_enabled,
        "question_count": question_count if question_count is not None else len(category.questions),
        "updated_at": iso(category.updated_at),
    }


def answer_json(answer, name=None):
    composed = compose_answer_markdown(answer, name or assistant_name())
    return {
        "id": answer.id,
        "question_id": answer.question_id,
        "body_markdown": answer.body_markdown,
        "links": answer.links or [],
        "template_id": answer.template_id,
        "template": {"id": answer.template.id, "name": answer.template.name} if answer.template else None,
        "priority": answer.priority,
        "is_enabled": answer.is_enabled,
        "rendered_html": render_markdown(composed),
        "updated_at": iso(answer.updated_at),
    }


def question_summary(question):
    active = question.active_answer
    return {
        "id": question.id,
        "text": question.text,
        "category": {"id": question.category.id, "name": question.category.name,
                     "is_enabled": question.category.is_enabled},
        "keywords": question.keywords or [],
        "is_suggested": question.is_suggested,
        "display_order": question.display_order,
        "is_enabled": question.is_enabled,
        "answer_count": len(question.answers),
        "has_active_answer": active is not None,
        "follow_up_count": len(question.follow_up_links),
        "updated_at": iso(question.updated_at),
    }


def question_detail(question):
    name = assistant_name()
    active = question.active_answer
    used_in_flows = db.session.scalar(
        select(func.count(func.distinct(ConversationFlowStep.flow_id)))
        .where(ConversationFlowStep.question_id == question.id)
    ) or 0
    follow_up_of = db.session.scalars(
        select(ConfiguredQuestion).join(FollowUpQuestion, FollowUpQuestion.parent_question_id == ConfiguredQuestion.id)
        .where(FollowUpQuestion.child_question_id == question.id)
    ).all()
    return {
        **question_summary(question),
        "category_id": question.category_id,
        "answers": [answer_json(a, name) for a in question.answers],
        "follow_ups": [
            {"id": link.child.id, "text": link.child.text, "is_enabled": link.child.is_enabled}
            for link in question.follow_up_links
        ],
        "follow_up_of": [{"id": q.id, "text": q.text} for q in follow_up_of],
        "used_in_flows": used_in_flows,
        # Exactly what AI Chat will show for this question.
        "preview": {
            "available": bool(question.is_enabled and question.category.is_enabled and active),
            "answer_html": render_markdown(compose_answer_markdown(active, name)) if active else None,
            "links": safe_links(active.links) if active else [],
            "follow_ups": [{"id": q.id, "text": q.text} for q in question.follow_ups],
        },
    }


def template_json(template, usage=None):
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "body_markdown": template.body_markdown,
        "usage_count": usage if usage is not None else db.session.scalar(
            select(func.count(ConfiguredAnswer.id)).where(ConfiguredAnswer.template_id == template.id)) or 0,
        "updated_at": iso(template.updated_at),
    }


def flow_tree(flow):
    by_parent = {}
    for step in flow.steps:
        by_parent.setdefault(step.parent_step_id, []).append(step)

    def build(step):
        children = sorted(by_parent.get(step.id, []), key=lambda s: s.step_order)
        return {
            "question_id": step.question_id,
            "text": step.question.text,
            "is_enabled": step.question.is_enabled,
            "children": [build(child) for child in children],
        }

    roots = sorted(by_parent.get(None, []), key=lambda s: s.step_order)
    return build(roots[0]) if roots else None


def flow_json(flow, with_tree=False):
    data = {
        "id": flow.id,
        "name": flow.name,
        "description": flow.description,
        "is_enabled": flow.is_enabled,
        "entry_question": {"id": flow.entry_question.id, "text": flow.entry_question.text} if flow.entry_question else None,
        "step_count": len(flow.steps),
        "updated_at": iso(flow.updated_at),
    }
    if with_tree:
        data["root"] = flow_tree(flow)
    return data


# =============================== Categories =======================================

CATEGORY_FIELDS = ("name", "slug", "description", "display_order", "is_enabled")


def _category_counts():
    return dict(db.session.execute(
        select(ConfiguredQuestion.category_id, func.count(ConfiguredQuestion.id)).group_by(ConfiguredQuestion.category_id)
    ).all())


@bp.get("/chat/categories")
@permission_required(READ)
def list_categories():
    stmt = select(QuestionCategory).order_by(QuestionCategory.display_order, QuestionCategory.name)
    if term := search_term():
        stmt = stmt.where(or_(QuestionCategory.name.ilike(term, escape="\\"),
                              QuestionCategory.description.ilike(term, escape="\\")))
    if status := request.args.get("status"):
        stmt = stmt.where(QuestionCategory.is_enabled.is_(status == "enabled"))
    counts = _category_counts()
    return jsonify(paginate(stmt, lambda c: category_json(c, counts.get(c.id, 0))))


@bp.get("/chat/categories/options")
@permission_required(READ)
def category_options():
    rows = db.session.scalars(select(QuestionCategory).order_by(QuestionCategory.display_order, QuestionCategory.name))
    return jsonify([{"id": c.id, "name": c.name, "is_enabled": c.is_enabled} for c in rows])


def _apply_category(category, data):
    duplicate = db.session.scalar(select(QuestionCategory.id).where(
        func.lower(QuestionCategory.name) == data.name.lower(), QuestionCategory.id != (category.id or 0)))
    if duplicate:
        raise ApiError("Please correct the highlighted fields.", 422, {"name": "A category with this name already exists."})
    category.name = data.name
    category.slug = unique_slug(data.slug or slugify(data.name), exclude_id=category.id)
    category.description = data.description
    category.display_order = data.display_order
    category.is_enabled = data.is_enabled


@bp.post("/chat/categories")
@permission_required(WRITE)
def create_category():
    data = parse_body(CategoryIn)
    category = QuestionCategory()
    _apply_category(category, data)
    db.session.add(category)
    db.session.flush()
    log_activity("create", category, summary=f"Created question category “{category.name}”",
                 after=snapshot(category, *CATEGORY_FIELDS))
    commit_or_conflict("name", "A category with this name already exists.")
    return jsonify(category_json(category, 0)), 201


@bp.put("/chat/categories/<int:category_id>")
@permission_required(WRITE)
def update_category(category_id):
    category = get_or_404(QuestionCategory, category_id, "Category")
    data = parse_body(CategoryIn)
    before = snapshot(category, *CATEGORY_FIELDS)
    _apply_category(category, data)
    log_activity("update", category, summary=f"Updated question category “{category.name}”",
                 before=before, after=snapshot(category, *CATEGORY_FIELDS))
    commit_or_conflict("name", "A category with this name already exists.")
    return jsonify(category_json(category))


@bp.delete("/chat/categories/<int:category_id>")
@permission_required(WRITE)
def delete_category(category_id):
    category = get_or_404(QuestionCategory, category_id, "Category")
    count = len(category.questions)
    if count:
        raise ApiError(
            f"“{category.name}” still has {count} question{'s' if count != 1 else ''}. "
            "Move or delete them first.", 409)
    log_activity("delete", category, summary=f"Deleted question category “{category.name}”",
                 before=snapshot(category, *CATEGORY_FIELDS))
    db.session.delete(category)
    db.session.commit()
    return "", 204


@bp.put("/chat/categories/order")
@permission_required(WRITE)
def reorder_categories():
    data = parse_body(ReorderIn)
    categories = {c.id: c for c in db.session.scalars(select(QuestionCategory).where(QuestionCategory.id.in_(data.ids)))}
    if len(categories) != len(data.ids):
        raise ApiError("Some categories no longer exist. Refresh and try again.", 409)
    for position, category_id in enumerate(data.ids, start=1):
        categories[category_id].display_order = position
    log_activity("update", entity_type="question_categories", summary="Reordered question categories",
                 after={"order": data.ids})
    db.session.commit()
    return jsonify({"ok": True})


# =============================== Questions ========================================

QUESTION_FIELDS = ("text", "category_id", "keywords", "is_suggested", "display_order", "is_enabled")


def _question_query():
    return select(ConfiguredQuestion).options(
        selectinload(ConfiguredQuestion.answers),
        selectinload(ConfiguredQuestion.follow_up_links),
        selectinload(ConfiguredQuestion.category),
    )


@bp.get("/chat/questions")
@permission_required(READ)
def list_questions():
    stmt = (_question_query().join(ConfiguredQuestion.category)
            .order_by(QuestionCategory.display_order, ConfiguredQuestion.display_order, ConfiguredQuestion.id))
    if category := request.args.get("category", type=int):
        stmt = stmt.where(ConfiguredQuestion.category_id == category)
    if status := request.args.get("status"):
        stmt = stmt.where(ConfiguredQuestion.is_enabled.is_(status == "enabled"))
    if suggested := request.args.get("suggested"):
        stmt = stmt.where(ConfiguredQuestion.is_suggested.is_(suggested == "yes"))
    if request.args.get("answer") == "missing":
        has_answer = select(ConfiguredAnswer.id).where(
            ConfiguredAnswer.question_id == ConfiguredQuestion.id, ConfiguredAnswer.is_enabled.is_(True)).exists()
        stmt = stmt.where(~has_answer)
    if term := search_term():
        stmt = stmt.where(or_(
            ConfiguredQuestion.text.ilike(term, escape="\\"),
            sa.cast(ConfiguredQuestion.keywords, sa.String).ilike(term, escape="\\"),
        ))
    return jsonify(paginate(stmt, question_summary))


@bp.get("/chat/questions/options")
@permission_required(READ)
def question_options():
    rows = db.session.scalars(
        select(ConfiguredQuestion).join(ConfiguredQuestion.category)
        .order_by(QuestionCategory.display_order, ConfiguredQuestion.display_order, ConfiguredQuestion.id))
    return jsonify([{"id": q.id, "text": q.text, "category": q.category.name, "is_enabled": q.is_enabled} for q in rows])


@bp.get("/chat/questions/suggested")
@permission_required(READ)
def suggested_questions():
    rows = db.session.scalars(
        select(ConfiguredQuestion).where(ConfiguredQuestion.is_suggested.is_(True))
        .order_by(ConfiguredQuestion.display_order, ConfiguredQuestion.id))
    return jsonify([{"id": q.id, "text": q.text, "is_enabled": q.is_enabled, "display_order": q.display_order}
                    for q in rows])


@bp.put("/chat/questions/suggested-order")
@permission_required(WRITE)
def reorder_suggested():
    data = parse_body(ReorderIn)
    questions = {q.id: q for q in db.session.scalars(
        select(ConfiguredQuestion).where(ConfiguredQuestion.id.in_(data.ids), ConfiguredQuestion.is_suggested.is_(True)))}
    if len(questions) != len(data.ids):
        raise ApiError("The list of suggested questions changed. Refresh and try again.", 409)
    for position, question_id in enumerate(data.ids, start=1):
        questions[question_id].display_order = position
    log_activity("update", entity_type="configured_questions", summary="Reordered suggested questions",
                 after={"order": data.ids})
    db.session.commit()
    return jsonify({"ok": True})


@bp.get("/chat/questions/<int:question_id>")
@permission_required(READ)
def get_question(question_id):
    return jsonify(question_detail(get_or_404(ConfiguredQuestion, question_id, "Question")))


def _apply_question(question, data):
    category = db.session.get(QuestionCategory, data.category_id)
    if category is None:
        raise ApiError("Please correct the highlighted fields.", 422, {"category_id": "Choose a category."})
    duplicate = db.session.scalar(select(ConfiguredQuestion.id).where(
        ConfiguredQuestion.category_id == category.id,
        func.lower(ConfiguredQuestion.text) == data.text.lower(),
        ConfiguredQuestion.id != (question.id or 0)))
    if duplicate:
        raise ApiError("Please correct the highlighted fields.", 422,
                       {"text": "This question already exists in that category."})
    was_suggested = bool(question.is_suggested)
    question.text = data.text
    question.category = category
    question.keywords = data.keywords
    question.is_suggested = data.is_suggested
    question.display_order = data.display_order
    question.is_enabled = data.is_enabled
    _place_new_suggestion_last(question, was_suggested)


def _place_new_suggestion_last(question, was_suggested):
    """A question that just became suggested (with no explicit order) goes to the end of the list."""
    if question.is_suggested and not was_suggested and not question.display_order:
        with db.session.no_autoflush:
            last = db.session.scalar(select(func.max(ConfiguredQuestion.display_order)).where(
                ConfiguredQuestion.is_suggested.is_(True), ConfiguredQuestion.id != (question.id or 0)))
        question.display_order = (last or 0) + 1


@bp.post("/chat/questions")
@permission_required(WRITE)
def create_question():
    data = parse_body(QuestionCreate)
    question = ConfiguredQuestion(created_by_id=current_user.id)
    _apply_question(question, data)
    if data.answer:
        question.answers.append(ConfiguredAnswer(body_markdown=data.answer))
    db.session.add(question)
    db.session.flush()
    log_activity("create", question, summary=f"Created question “{question.text[:150]}”",
                 after={**snapshot(question, *QUESTION_FIELDS), "answer": data.answer})
    commit_or_conflict("text", "This question already exists in that category.")
    return jsonify(question_detail(question)), 201


@bp.put("/chat/questions/<int:question_id>")
@permission_required(WRITE)
def update_question(question_id):
    question = get_or_404(ConfiguredQuestion, question_id, "Question")
    data = parse_body(QuestionIn)
    before = snapshot(question, *QUESTION_FIELDS)
    _apply_question(question, data)
    log_activity("update", question, summary=f"Updated question “{question.text[:150]}”",
                 before=before, after=snapshot(question, *QUESTION_FIELDS))
    commit_or_conflict("text", "This question already exists in that category.")
    return jsonify(question_detail(question))


@bp.patch("/chat/questions/<int:question_id>")
@permission_required(WRITE)
def patch_question(question_id):
    question = get_or_404(ConfiguredQuestion, question_id, "Question")
    data = parse_body(QuestionPatch)
    before = snapshot(question, "is_enabled", "is_suggested")
    changes = data.model_dump(exclude_none=True)
    was_suggested = question.is_suggested
    for field, value in changes.items():
        setattr(question, field, value)
    if changes.get("is_suggested") and not was_suggested:
        question.display_order = 0
        _place_new_suggestion_last(question, was_suggested)
    words = ", ".join(f"{k.removeprefix('is_')} = {'yes' if v else 'no'}" for k, v in changes.items())
    log_activity("update", question, summary=f"Set {words} on “{question.text[:120]}”",
                 before=before, after=snapshot(question, "is_enabled", "is_suggested"))
    db.session.commit()
    return jsonify(question_summary(question))


@bp.delete("/chat/questions/<int:question_id>")
@permission_required(WRITE)
def delete_question(question_id):
    question = get_or_404(ConfiguredQuestion, question_id, "Question")
    log_activity("delete", question, summary=f"Deleted question “{question.text[:150]}”",
                 before={**snapshot(question, *QUESTION_FIELDS),
                         "answers": [a.body_markdown for a in question.answers]})
    db.session.delete(question)  # answers, follow-up links and flow steps go with it (FK cascade)
    db.session.commit()
    return "", 204


@bp.put("/chat/questions/<int:question_id>/follow-ups")
@permission_required(WRITE)
def set_follow_ups(question_id):
    question = get_or_404(ConfiguredQuestion, question_id, "Question")
    data = parse_body(FollowUpsIn)
    if question.id in data.question_ids:
        raise ApiError("A question can't be its own follow-up.", 422, {"question_ids": "A question can't follow itself."})
    found = {q.id for q in db.session.scalars(
        select(ConfiguredQuestion).where(ConfiguredQuestion.id.in_(data.question_ids)))}
    if missing := set(data.question_ids) - found:
        raise ApiError(f"Some selected questions no longer exist ({len(missing)}). Refresh and try again.", 409)

    before = [link.child_question_id for link in question.follow_up_links]
    question.follow_up_links.clear()
    db.session.flush()
    for position, child_id in enumerate(data.question_ids, start=1):
        question.follow_up_links.append(FollowUpQuestion(child_question_id=child_id, display_order=position))
    log_activity("update", question, summary=f"Updated follow-ups of “{question.text[:120]}”",
                 before={"follow_ups": before}, after={"follow_ups": data.question_ids})
    db.session.commit()
    db.session.refresh(question)
    return jsonify(question_detail(question))


# =============================== Answers ==========================================

ANSWER_FIELDS = ("body_markdown", "links", "template_id", "priority", "is_enabled")


def _apply_answer(answer, data):
    if data.template_id is not None and db.session.get(AnswerTemplate, data.template_id) is None:
        raise ApiError("Please correct the highlighted fields.", 422, {"template_id": "This template no longer exists."})
    answer.body_markdown = data.body_markdown
    answer.links = [link.model_dump() for link in data.links]
    answer.template_id = data.template_id
    answer.priority = data.priority
    answer.is_enabled = data.is_enabled


@bp.post("/chat/questions/<int:question_id>/answers")
@permission_required(WRITE)
def create_answer(question_id):
    question = get_or_404(ConfiguredQuestion, question_id, "Question")
    data = parse_body(AnswerIn)
    answer = ConfiguredAnswer(question=question)
    _apply_answer(answer, data)
    db.session.add(answer)
    db.session.flush()
    log_activity("create", answer, summary=f"Added an answer to “{question.text[:120]}”",
                 after=snapshot(answer, *ANSWER_FIELDS))
    db.session.commit()
    return jsonify(answer_json(answer)), 201


@bp.put("/chat/answers/<int:answer_id>")
@permission_required(WRITE)
def update_answer(answer_id):
    answer = get_or_404(ConfiguredAnswer, answer_id, "Answer")
    data = parse_body(AnswerIn)
    before = snapshot(answer, *ANSWER_FIELDS)
    _apply_answer(answer, data)
    log_activity("update", answer, summary=f"Edited an answer to “{answer.question.text[:120]}”",
                 before=before, after=snapshot(answer, *ANSWER_FIELDS))
    db.session.commit()
    return jsonify(answer_json(answer))


@bp.patch("/chat/answers/<int:answer_id>")
@permission_required(WRITE)
def patch_answer(answer_id):
    answer = get_or_404(ConfiguredAnswer, answer_id, "Answer")
    data = parse_body(AnswerPatch)
    answer.is_enabled = data.is_enabled
    log_activity("update", answer, summary=f"{'Enabled' if data.is_enabled else 'Disabled'} an answer to "
                 f"“{answer.question.text[:120]}”", after={"is_enabled": data.is_enabled})
    db.session.commit()
    return jsonify(answer_json(answer))


@bp.delete("/chat/answers/<int:answer_id>")
@permission_required(WRITE)
def delete_answer(answer_id):
    answer = get_or_404(ConfiguredAnswer, answer_id, "Answer")
    log_activity("delete", answer, summary=f"Deleted an answer to “{answer.question.text[:120]}”",
                 before=snapshot(answer, *ANSWER_FIELDS))
    db.session.delete(answer)
    db.session.commit()
    return "", 204


@bp.post("/chat/preview")
@permission_required(READ)
def preview_markdown():
    """Live preview for the answer editor (applies the chosen template)."""
    data = parse_body(PreviewIn)
    template = db.session.get(AnswerTemplate, data.template_id) if data.template_id else None
    body = data.body_markdown
    if template:
        body = template.body_markdown.replace("{answer}", body)
    return jsonify({"html": render_markdown(body.replace("{assistant_name}", assistant_name()))})


# =============================== Templates ========================================

TEMPLATE_FIELDS = ("name", "description", "body_markdown")


@bp.get("/chat/templates")
@permission_required(READ)
def list_templates():
    stmt = select(AnswerTemplate).order_by(AnswerTemplate.name)
    if term := search_term():
        stmt = stmt.where(or_(AnswerTemplate.name.ilike(term, escape="\\"),
                              AnswerTemplate.body_markdown.ilike(term, escape="\\")))
    usage = dict(db.session.execute(
        select(ConfiguredAnswer.template_id, func.count(ConfiguredAnswer.id))
        .where(ConfiguredAnswer.template_id.is_not(None)).group_by(ConfiguredAnswer.template_id)).all())
    return jsonify(paginate(stmt, lambda t: template_json(t, usage.get(t.id, 0))))


@bp.get("/chat/templates/options")
@permission_required(READ)
def template_options():
    rows = db.session.scalars(select(AnswerTemplate).order_by(AnswerTemplate.name))
    return jsonify([{"id": t.id, "name": t.name} for t in rows])


def _check_template_name(name, exclude_id=None):
    duplicate = db.session.scalar(select(AnswerTemplate.id).where(
        func.lower(AnswerTemplate.name) == name.lower(), AnswerTemplate.id != (exclude_id or 0)))
    if duplicate:
        raise ApiError("Please correct the highlighted fields.", 422, {"name": "A template with this name already exists."})


@bp.post("/chat/templates")
@permission_required(WRITE)
def create_template():
    data = parse_body(TemplateIn)
    _check_template_name(data.name)
    template = AnswerTemplate(**data.model_dump())
    db.session.add(template)
    db.session.flush()
    log_activity("create", template, summary=f"Created answer template “{template.name}”",
                 after=snapshot(template, *TEMPLATE_FIELDS))
    commit_or_conflict("name", "A template with this name already exists.")
    return jsonify(template_json(template, 0)), 201


@bp.put("/chat/templates/<int:template_id>")
@permission_required(WRITE)
def update_template(template_id):
    template = get_or_404(AnswerTemplate, template_id, "Template")
    data = parse_body(TemplateIn)
    _check_template_name(data.name, template.id)
    before = snapshot(template, *TEMPLATE_FIELDS)
    for field, value in data.model_dump().items():
        setattr(template, field, value)
    log_activity("update", template, summary=f"Updated answer template “{template.name}”",
                 before=before, after=snapshot(template, *TEMPLATE_FIELDS))
    commit_or_conflict("name", "A template with this name already exists.")
    return jsonify(template_json(template))


@bp.delete("/chat/templates/<int:template_id>")
@permission_required(WRITE)
def delete_template(template_id):
    template = get_or_404(AnswerTemplate, template_id, "Template")
    log_activity("delete", template, summary=f"Deleted answer template “{template.name}”",
                 before=snapshot(template, *TEMPLATE_FIELDS))
    db.session.delete(template)  # answers using it keep their text (template_id -> NULL)
    db.session.commit()
    return "", 204


# =============================== Conversation flows ================================

@bp.get("/chat/flows")
@permission_required(READ)
def list_flows():
    stmt = (select(ConversationFlow)
            .options(selectinload(ConversationFlow.steps), selectinload(ConversationFlow.entry_question))
            .order_by(ConversationFlow.name))
    if term := search_term():
        stmt = stmt.where(or_(ConversationFlow.name.ilike(term, escape="\\"),
                              ConversationFlow.description.ilike(term, escape="\\")))
    if status := request.args.get("status"):
        stmt = stmt.where(ConversationFlow.is_enabled.is_(status == "enabled"))
    return jsonify(paginate(stmt, flow_json))


@bp.get("/chat/flows/<int:flow_id>")
@permission_required(READ)
def get_flow(flow_id):
    return jsonify(flow_json(get_or_404(ConversationFlow, flow_id, "Flow"), with_tree=True))


def _flow_question_ids(step, acc):
    acc.add(step.question_id)
    for child in step.children:
        _flow_question_ids(child, acc)
    return acc


def _apply_flow(flow, data):
    duplicate = db.session.scalar(select(ConversationFlow.id).where(
        func.lower(ConversationFlow.name) == data.name.lower(), ConversationFlow.id != (flow.id or 0)))
    if duplicate:
        raise ApiError("Please correct the highlighted fields.", 422, {"name": "A flow with this name already exists."})

    if data.root is not None:
        ids = _flow_question_ids(data.root, set())
        found = set(db.session.scalars(select(ConfiguredQuestion.id).where(ConfiguredQuestion.id.in_(ids))))
        if ids - found:
            raise ApiError("Some questions in this flow no longer exist. Refresh and try again.", 409)

    flow.name = data.name
    flow.description = data.description
    flow.is_enabled = data.is_enabled

    # Replace the whole tree: simpler and safer than diffing nested steps.
    if flow.id is not None:
        flow.steps.clear()
        db.session.flush()
    flow.entry_question_id = data.root.question_id if data.root else None

    def add(step_in, parent, order):
        step = ConversationFlowStep(question_id=step_in.question_id, step_order=order, parent=parent)
        flow.steps.append(step)
        for child_order, child in enumerate(step_in.children, start=1):
            add(child, step, child_order)

    if data.root is not None:
        add(data.root, None, 0)


@bp.post("/chat/flows")
@permission_required(WRITE)
def create_flow():
    data = parse_body(FlowIn)
    flow = ConversationFlow()
    _apply_flow(flow, data)  # validates first; the flow joins the session only once it's complete
    db.session.add(flow)
    db.session.flush()
    log_activity("create", flow, summary=f"Created conversation flow “{flow.name}”",
                 after=data.model_dump())
    commit_or_conflict("name", "A flow with this name already exists.")
    return jsonify(flow_json(flow, with_tree=True)), 201


@bp.put("/chat/flows/<int:flow_id>")
@permission_required(WRITE)
def update_flow(flow_id):
    flow = get_or_404(ConversationFlow, flow_id, "Flow")
    data = parse_body(FlowIn)
    before = {**snapshot(flow, "name", "description", "is_enabled"), "root": flow_tree(flow)}
    _apply_flow(flow, data)
    log_activity("update", flow, summary=f"Updated conversation flow “{flow.name}”",
                 before=before, after=data.model_dump())
    commit_or_conflict("name", "A flow with this name already exists.")
    db.session.refresh(flow)
    return jsonify(flow_json(flow, with_tree=True))


@bp.delete("/chat/flows/<int:flow_id>")
@permission_required(WRITE)
def delete_flow(flow_id):
    flow = get_or_404(ConversationFlow, flow_id, "Flow")
    log_activity("delete", flow, summary=f"Deleted conversation flow “{flow.name}”",
                 before={**snapshot(flow, "name", "description", "is_enabled"), "root": flow_tree(flow)})
    db.session.delete(flow)
    db.session.commit()
    return "", 204


# =============================== Fallback answer ===================================

@bp.get("/chat/fallback")
@permission_required(READ)
def get_fallback():
    return jsonify({"fallback_answer": AssistantSettings.get().fallback_answer})


@bp.put("/chat/fallback")
@permission_required(WRITE)
def update_fallback():
    data = parse_body(FallbackIn)
    settings = AssistantSettings.get()
    before = settings.fallback_answer
    settings.fallback_answer = data.fallback_answer
    log_activity("update", settings, summary="Updated the fallback answer",
                 before={"fallback_answer": before}, after={"fallback_answer": data.fallback_answer})
    db.session.commit()
    return jsonify({"fallback_answer": settings.fallback_answer})
