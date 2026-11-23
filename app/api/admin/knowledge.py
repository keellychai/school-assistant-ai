"""Knowledge base API: categories, entries, documents and a match tester.

Only entries that are published AND available to AI are used by AI Chat.
Documents are stored privately and downloaded through this (admin-only) API.
"""

import sqlalchemy as sa
from flask import jsonify, request, send_file
from flask_login import current_user
from sqlalchemy import func, or_, select

from ...auth.security import permission_required
from ...extensions import db
from ...models import KnowledgeCategory, KnowledgeEntry
from ...services.audit import log_activity
from ...services.classifier import predict_category
from ...services.documents import DocumentError, delete_document, document_file, save_document
from ...services.knowledge_search import MIN_SCORE, rank_knowledge, search_knowledge
from ...services.markdown_render import render_markdown
from . import bp
from .chat_config import slugify
from .schemas import KnowledgeCategoryIn, KnowledgeEntryIn, KnowledgePatch, PreviewIn, TestMatchIn
from .utils import ApiError, iso, paginate, parse_body, search_term

READ = "knowledge.read"
WRITE = "knowledge.write"
ENTRY_FIELDS = ("title", "category_id", "external_ref", "content_markdown", "keywords", "source_ref",
                "available_to_ai", "is_published")


def _get(model, object_id, label):
    obj = db.session.get(model, object_id)
    if obj is None:
        raise ApiError(f"{label} not found.", 404)
    return obj


def _snapshot(entry):
    data = {field: getattr(entry, field) for field in ENTRY_FIELDS}
    data["document_name"] = entry.document_name
    return data


def _user_name(user_id):
    from ...models import User
    user = db.session.get(User, user_id) if user_id else None
    return user.full_name if user else None


def category_json(category, count=None):
    return {"id": category.id, "name": category.name, "slug": category.slug, "description": category.description,
            "entry_count": count if count is not None else len(category.entries), "updated_at": iso(category.updated_at)}


def entry_summary(entry):
    return {
        "id": entry.id,
        "title": entry.title,
        "external_ref": entry.external_ref,
        "category": {"id": entry.category.id, "name": entry.category.name},
        "keywords": entry.keywords or [],
        "excerpt": " ".join((entry.content_markdown or "").split())[:160],
        "available_to_ai": entry.available_to_ai,
        "is_published": entry.is_published,
        "used_by_chat": entry.available_to_ai and entry.is_published,
        "document": {"name": entry.document_name, "type": entry.document_type, "size": entry.document_size}
                    if entry.document_path else None,
        "updated_at": iso(entry.updated_at),
    }


def entry_detail(entry):
    text = entry.document_text or ""
    return {
        **entry_summary(entry),
        "category_id": entry.category_id,
        "content_markdown": entry.content_markdown,
        "content_html": render_markdown(entry.content_markdown),
        "source_ref": entry.source_ref,
        "document_text_preview": text[:3000],
        "document_text_length": len(text),
        "created_by": _user_name(entry.created_by_id),
        "updated_by": _user_name(entry.updated_by_id),
        "created_at": iso(entry.created_at),
    }


# =============================== Categories ============================================

def _counts():
    return dict(db.session.execute(select(KnowledgeEntry.category_id, func.count(KnowledgeEntry.id))
                                   .group_by(KnowledgeEntry.category_id)).all())


@bp.get("/knowledge/categories")
@permission_required(READ)
def list_knowledge_categories():
    stmt = select(KnowledgeCategory).order_by(KnowledgeCategory.name)
    if term := search_term():
        stmt = stmt.where(or_(KnowledgeCategory.name.ilike(term, escape="\\"),
                              KnowledgeCategory.description.ilike(term, escape="\\")))
    counts = _counts()
    return jsonify(paginate(stmt, lambda c: category_json(c, counts.get(c.id, 0))))


@bp.get("/knowledge/categories/options")
@permission_required(READ)
def knowledge_category_options():
    return jsonify([{"id": c.id, "name": c.name} for c in
                    db.session.scalars(select(KnowledgeCategory).order_by(KnowledgeCategory.name))])


def _apply_category(category, data):
    duplicate = db.session.scalar(select(KnowledgeCategory.id).where(
        func.lower(KnowledgeCategory.name) == data.name.lower(), KnowledgeCategory.id != (category.id or 0)))
    if duplicate:
        raise ApiError("Please correct the highlighted fields.", 422, {"name": "A category with this name already exists."})
    base, slug, n = data.slug or slugify(data.name), None, 1
    slug = base
    while db.session.scalar(select(KnowledgeCategory.id).where(
            KnowledgeCategory.slug == slug, KnowledgeCategory.id != (category.id or 0))):
        n += 1
        slug = f"{base}-{n}"
    category.name, category.slug, category.description = data.name, slug, data.description


@bp.post("/knowledge/categories")
@permission_required(WRITE)
def create_knowledge_category():
    data = parse_body(KnowledgeCategoryIn)
    category = KnowledgeCategory()
    _apply_category(category, data)
    db.session.add(category)
    db.session.flush()
    log_activity("create", category, summary=f"Created knowledge category “{category.name}”",
                 after={"name": category.name, "slug": category.slug})
    db.session.commit()
    return jsonify(category_json(category, 0)), 201


@bp.put("/knowledge/categories/<int:category_id>")
@permission_required(WRITE)
def update_knowledge_category(category_id):
    category = _get(KnowledgeCategory, category_id, "Category")
    data = parse_body(KnowledgeCategoryIn)
    before = {"name": category.name, "slug": category.slug, "description": category.description}
    _apply_category(category, data)
    log_activity("update", category, summary=f"Updated knowledge category “{category.name}”", before=before,
                 after={"name": category.name, "slug": category.slug, "description": category.description})
    db.session.commit()
    return jsonify(category_json(category))


@bp.delete("/knowledge/categories/<int:category_id>")
@permission_required(WRITE)
def delete_knowledge_category(category_id):
    category = _get(KnowledgeCategory, category_id, "Category")
    count = len(category.entries)
    if count:
        raise ApiError(f"“{category.name}” still has {count} entr{'ies' if count != 1 else 'y'}. "
                       "Move or delete them first.", 409)
    log_activity("delete", category, summary=f"Deleted knowledge category “{category.name}”",
                 before={"name": category.name, "slug": category.slug})
    db.session.delete(category)
    db.session.commit()
    return "", 204


# =============================== Entries ===============================================

@bp.get("/knowledge")
@permission_required(READ)
def list_knowledge():
    stmt = select(KnowledgeEntry).join(KnowledgeEntry.category).order_by(KnowledgeEntry.updated_at.desc(), KnowledgeEntry.id.desc())
    if category := request.args.get("category", type=int):
        stmt = stmt.where(KnowledgeEntry.category_id == category)
    usage = request.args.get("usage")
    if usage == "chat":
        stmt = stmt.where(KnowledgeEntry.is_published.is_(True), KnowledgeEntry.available_to_ai.is_(True))
    elif usage == "hidden":
        stmt = stmt.where(or_(KnowledgeEntry.is_published.is_(False), KnowledgeEntry.available_to_ai.is_(False)))
    elif usage == "draft":
        stmt = stmt.where(KnowledgeEntry.is_published.is_(False))
    if request.args.get("document") == "yes":
        stmt = stmt.where(KnowledgeEntry.document_path.is_not(None))
    if term := search_term():
        stmt = stmt.where(or_(
            KnowledgeEntry.title.ilike(term, escape="\\"),
            KnowledgeEntry.content_markdown.ilike(term, escape="\\"),
            KnowledgeEntry.external_ref.ilike(term, escape="\\"),
            KnowledgeEntry.document_text.ilike(term, escape="\\"),
            sa.cast(KnowledgeEntry.keywords, sa.String).ilike(term, escape="\\"),
        ))
    return jsonify(paginate(stmt, entry_summary))


@bp.get("/knowledge/summary")
@permission_required(READ)
def knowledge_summary():
    total = db.session.scalar(select(func.count(KnowledgeEntry.id))) or 0
    in_chat = db.session.scalar(select(func.count(KnowledgeEntry.id)).where(
        KnowledgeEntry.is_published.is_(True), KnowledgeEntry.available_to_ai.is_(True))) or 0
    documents = db.session.scalar(select(func.count(KnowledgeEntry.id)).where(KnowledgeEntry.document_path.is_not(None))) or 0
    categories = db.session.scalar(select(func.count(KnowledgeCategory.id))) or 0
    return jsonify({"total": total, "in_chat": in_chat, "hidden": total - in_chat, "documents": documents,
                    "categories": categories})


@bp.get("/knowledge/<int:entry_id>")
@permission_required(READ)
def get_knowledge(entry_id):
    return jsonify(entry_detail(_get(KnowledgeEntry, entry_id, "Knowledge entry")))


def _apply_entry(entry, data):
    errors = {}
    if db.session.get(KnowledgeCategory, data.category_id) is None:
        errors["category_id"] = "Choose a category."
    if data.external_ref and db.session.scalar(select(KnowledgeEntry.id).where(
            KnowledgeEntry.external_ref == data.external_ref, KnowledgeEntry.id != (entry.id or 0))):
        errors["external_ref"] = "Another entry already uses this reference."
    if errors:
        raise ApiError("Please correct the highlighted fields.", 422, errors)
    for field in ENTRY_FIELDS:
        setattr(entry, field, getattr(data, field))
    entry.updated_by_id = current_user.id


@bp.post("/knowledge")
@permission_required(WRITE)
def create_knowledge():
    data = parse_body(KnowledgeEntryIn)
    entry = KnowledgeEntry(created_by_id=current_user.id)
    _apply_entry(entry, data)
    db.session.add(entry)
    db.session.flush()
    log_activity("create", entry, summary=f"Created knowledge entry “{entry.title}”", after=_snapshot(entry))
    db.session.commit()
    return jsonify(entry_detail(entry)), 201


@bp.put("/knowledge/<int:entry_id>")
@permission_required(WRITE)
def update_knowledge(entry_id):
    entry = _get(KnowledgeEntry, entry_id, "Knowledge entry")
    data = parse_body(KnowledgeEntryIn)
    before = _snapshot(entry)
    _apply_entry(entry, data)
    log_activity("update", entry, summary=f"Updated knowledge entry “{entry.title}”", before=before, after=_snapshot(entry))
    db.session.commit()
    return jsonify(entry_detail(entry))


@bp.patch("/knowledge/<int:entry_id>")
@permission_required(WRITE)
def patch_knowledge(entry_id):
    entry = _get(KnowledgeEntry, entry_id, "Knowledge entry")
    data = parse_body(KnowledgePatch)
    changes = data.model_dump(exclude_none=True)
    before = {k: getattr(entry, k) for k in changes}
    for field, value in changes.items():
        setattr(entry, field, value)
    entry.updated_by_id = current_user.id
    labels = {"is_published": "published", "available_to_ai": "available to AI Chat"}
    words = ", ".join(f"{labels[k]} = {'yes' if v else 'no'}" for k, v in changes.items())
    log_activity("update", entry, summary=f"Set {words} on “{entry.title[:120]}”", before=before, after=changes)
    db.session.commit()
    return jsonify(entry_summary(entry))


@bp.delete("/knowledge/<int:entry_id>")
@permission_required(WRITE)
def delete_knowledge(entry_id):
    entry = _get(KnowledgeEntry, entry_id, "Knowledge entry")
    path = entry.document_path
    log_activity("delete", entry, summary=f"Deleted knowledge entry “{entry.title}”", before=_snapshot(entry))
    db.session.delete(entry)
    db.session.commit()
    delete_document(path)
    return "", 204


# =============================== Documents =============================================

@bp.post("/knowledge/<int:entry_id>/document")
@permission_required(WRITE)
def upload_knowledge_document(entry_id):
    entry = _get(KnowledgeEntry, entry_id, "Knowledge entry")
    try:
        saved = save_document(request.files.get("document"))
    except DocumentError as error:
        raise ApiError(str(error), 422, {"document": str(error)}) from error
    old = entry.document_path
    entry.document_path, entry.document_name = saved["path"], saved["name"]
    entry.document_type, entry.document_size, entry.document_text = saved["type"], saved["size"], saved["text"]
    entry.updated_by_id = current_user.id
    log_activity("update", entry, summary=f"Attached “{saved['name']}” to “{entry.title[:120]}”",
                 after={"document_name": saved["name"], "document_size": saved["size"], "text_chars": len(saved["text"])})
    db.session.commit()
    delete_document(old)
    return jsonify(entry_detail(entry)), 201


@bp.get("/knowledge/<int:entry_id>/document")
@permission_required(READ)
def download_knowledge_document(entry_id):
    entry = _get(KnowledgeEntry, entry_id, "Knowledge entry")
    path = document_file(entry.document_path)
    if path is None:
        raise ApiError("This entry has no document.", 404)
    mimetypes = {"pdf": "application/pdf", "txt": "text/plain; charset=utf-8", "md": "text/markdown; charset=utf-8",
                 "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    response = send_file(path, mimetype=mimetypes.get(entry.document_type, "application/octet-stream"),
                         as_attachment=True, download_name=entry.document_name or path.name)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.delete("/knowledge/<int:entry_id>/document")
@permission_required(WRITE)
def remove_knowledge_document(entry_id):
    entry = _get(KnowledgeEntry, entry_id, "Knowledge entry")
    old, name = entry.document_path, entry.document_name
    if old:
        entry.document_path = entry.document_name = entry.document_type = entry.document_text = None
        entry.document_size = None
        entry.updated_by_id = current_user.id
        log_activity("update", entry, summary=f"Removed “{name}” from “{entry.title[:120]}”", before={"document_name": name})
        db.session.commit()
        delete_document(old)
    return "", 204


# =============================== Preview & match tester =================================

@bp.post("/knowledge/preview")
@permission_required(READ)
def knowledge_preview():
    data = parse_body(PreviewIn)
    return jsonify({"html": render_markdown(data.body_markdown)})


@bp.post("/knowledge/test-match")
@permission_required(READ)
def test_match():
    """Which entry would AI Chat use for this question? Shows the top candidates and why."""
    data = parse_body(TestMatchIn)
    category, confidence = predict_category(data.question)
    chosen = search_knowledge(data.question, category=category)
    ranked = rank_knowledge(data.question, category=category, include_unavailable=True)[:5]
    return jsonify({
        "question": data.question,
        "predicted_category": category,
        "confidence": confidence,
        "answer": entry_summary(chosen) if chosen else None,
        "min_score": MIN_SCORE,
        "candidates": [{**entry_summary(entry), "score": round(score, 2), "keyword_match": has_keyword}
                       for score, has_keyword, entry in ranked],
    })
