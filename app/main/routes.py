"""Existing student-facing pages.

These are kept working during Phase 1. `/api/ask` is a transitional endpoint:
it now reads knowledge from the database, and will be replaced by the full
chat engine (configured Q&A -> knowledge -> AI -> fallback) in Phase 2.
"""

from flask import jsonify, render_template, request

from ..extensions import limiter
from ..models import AssistantSettings
from ..services.classifier import predict_category
from ..services.knowledge_search import search_knowledge
from . import bp

MAX_QUESTION_LENGTH = 500


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@bp.route("/chatbot")
def chatbot():
    return render_template("chatbot.html")


@bp.route("/api/ask", methods=["POST"])
@limiter.limit("30 per minute")
def ask():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()

    if not question:
        return jsonify({"error": "Please enter a question."}), 400
    if len(question) > MAX_QUESTION_LENGTH:
        return jsonify({"error": f"Please keep your question under {MAX_QUESTION_LENGTH} characters."}), 400

    category, confidence = predict_category(question)
    entry = search_knowledge(question, category=category)

    if entry is None:
        return jsonify({
            "answer": AssistantSettings.get().fallback_answer,
            "source": None,
            "category": category,
            "confidence": confidence,
            "needs_review": True,
        })

    return jsonify({
        "answer": entry.content_markdown,
        "source": {"id": entry.external_ref or str(entry.id), "title": entry.title},
        "category": entry.category.slug,
        "confidence": confidence,
        "needs_review": False,
    })
