"""Step 4: AI Chat configuration (categories, questions, answers, follow-ups, templates, flows, fallback)."""

import re

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    ActivityLog,
    ConfiguredAnswer,
    ConfiguredQuestion,
    ConversationFlowStep,
    FollowUpQuestion,
    QuestionCategory,
)
from app.services.markdown_render import is_safe_url, render_markdown

from .conftest import ask, login

API = "/api/admin/chat"


def q_by_text(text):
    return db.session.scalar(select(ConfiguredQuestion).where(ConfiguredQuestion.text == text))


def category_id(slug):
    return db.session.scalar(select(QuestionCategory.id).where(QuestionCategory.slug == slug))


def new_question(client, **overrides):
    body = {"text": "Where is the library?", "category_id": category_id("administrasi"),
            "keywords": ["library"], "is_suggested": False, "display_order": 0, "is_enabled": True,
            "answer": "Next to the **science lab**.", **overrides}
    response = client.post(f"{API}/questions", json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()


# --- Markdown safety ------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))",
    "[x](jav&#x09;ascript:alert(1))",
    "[x](&#106;avascript:alert(1))",
    "[x](data:text/html,hi)",
    "![x](javascript:alert(1))",
    "<a href='javascript:alert(1)'>x</a>",
])
def test_markdown_never_emits_script(source):
    html = render_markdown(source).lower()
    tags = re.findall(r"<[a-z][^>]*>", html)  # real tags only; escaped text (&lt;...) is harmless
    assert not any(t.startswith(("<script", "<iframe", "<object", "<embed")) for t in tags)
    assert not any(re.search(r"\son[a-z]+\s*=", t) for t in tags), tags
    assert not any(re.search(r"(href|src)\s*=\s*[\"']?\s*(javascript|data|vbscript):", t) for t in tags), tags


def test_markdown_keeps_formatting_and_safe_links():
    html = render_markdown("**Bold** and [site](https://example.com) and [page](/admin)")
    assert "<strong>Bold</strong>" in html
    assert 'href="https://example.com"' in html and 'rel="noopener noreferrer nofollow"' in html
    assert 'href="/admin"' in html


@pytest.mark.parametrize("url,ok", [
    ("https://example.com", True), ("mailto:a@b.c", True), ("/path", True), ("#top", True),
    ("javascript:alert(1)", False), ("//evil.com", False), ("www.example.com", False), ("data:x", False),
])
def test_is_safe_url(url, ok):
    assert is_safe_url(url) is ok


# --- Categories ---------------------------------------------------------------------------

def test_category_crud_and_slug(admin_client):
    created = admin_client.post(f"{API}/categories", json={"name": "Ekstra Kurikuler"}).get_json()
    assert created["slug"] == "ekstra-kurikuler"
    second = admin_client.post(f"{API}/categories", json={"name": "Other", "slug": "ekstra-kurikuler"}).get_json()
    assert second["slug"] == "ekstra-kurikuler-2"  # made unique automatically

    updated = admin_client.put(f"{API}/categories/{created['id']}", json={
        "name": "Extracurricular", "slug": "", "description": "Clubs", "display_order": 9, "is_enabled": False}).get_json()
    assert updated["name"] == "Extracurricular" and updated["is_enabled"] is False

    assert admin_client.delete(f"{API}/categories/{created['id']}").status_code == 204
    assert db.session.get(QuestionCategory, created["id"]) is None


def test_category_validation(admin_client):
    response = admin_client.post(f"{API}/categories", json={"name": "x", "slug": "Bad Slug!"})
    assert response.status_code == 422
    assert set(response.get_json()["fields"]) == {"name", "slug"}
    response = admin_client.post(f"{API}/categories", json={"name": "jadwal"})  # case-insensitive duplicate
    assert response.status_code == 422 and "name" in response.get_json()["fields"]


def test_category_with_questions_cannot_be_deleted(admin_client):
    response = admin_client.delete(f"{API}/categories/{category_id('business')}")
    assert response.status_code == 409 and "still has 4 questions" in response.get_json()["error"]


def test_unknown_fields_are_rejected(admin_client):
    response = admin_client.post(f"{API}/categories", json={"name": "Valid", "is_admin": True})
    assert response.status_code == 422 and "is_admin" in response.get_json()["fields"]


# --- Questions -------------------------------------------------------------------------------

def test_create_question_with_first_answer(admin_client):
    data = new_question(admin_client, keywords=[" Library ", "library", "BOOKS"])
    assert data["keywords"] == ["library", "books"]  # trimmed, lower-cased, de-duplicated
    assert data["answers"][0]["rendered_html"] == "<p>Next to the <strong>science lab</strong>.</p>"
    assert data["preview"]["available"] is True


def test_question_validation_messages(admin_client):
    response = admin_client.post(f"{API}/questions", json={"text": "hi", "category_id": None})
    fields = response.get_json()["fields"]
    assert response.status_code == 422 and fields["category_id"] == "Choose a category."
    assert "text" in fields


def test_duplicate_question_in_same_category_rejected(admin_client):
    response = admin_client.post(f"{API}/questions", json={
        "text": "how do i create an event?", "category_id": category_id("business")})
    assert response.status_code == 422 and "already exists" in response.get_json()["fields"]["text"]


def test_question_list_filters(admin_client):
    new_question(admin_client, answer=None, text="Question without answer")
    suggested = admin_client.get(f"{API}/questions?suggested=yes").get_json()
    assert suggested["total"] == 4
    missing = admin_client.get(f"{API}/questions?answer=missing").get_json()
    assert [q["text"] for q in missing["items"]] == ["Question without answer"]
    by_keyword = admin_client.get(f"{API}/questions?q=remove event").get_json()
    assert by_keyword["items"][0]["text"] == "How do I delete an event?"
    by_category = admin_client.get(f"{API}/questions?category={category_id('business')}").get_json()
    assert by_category["total"] == 4


def test_toggle_question_and_preview_availability(admin_client):
    q = q_by_text("How do I edit an event?")
    admin_client.patch(f"{API}/questions/{q.id}", json={"is_enabled": False})
    detail = admin_client.get(f"{API}/questions/{q.id}").get_json()
    assert detail["is_enabled"] is False and detail["preview"]["available"] is False

    parent = q_by_text("How do I manage my business schedule?")
    follow_ups = admin_client.get(f"{API}/questions/{parent.id}").get_json()["preview"]["follow_ups"]
    assert "How do I edit an event?" not in [f["text"] for f in follow_ups]  # disabled ones are hidden


def test_disabled_category_hides_question_in_preview(admin_client):
    category = db.session.get(QuestionCategory, category_id("business"))
    category.is_enabled = False
    db.session.commit()
    q = q_by_text("How do I create an event?")
    assert admin_client.get(f"{API}/questions/{q.id}").get_json()["preview"]["available"] is False


def test_delete_question_cascades(admin_client):
    parent = q_by_text("How do I manage my business schedule?")
    assert admin_client.delete(f"{API}/questions/{parent.id}").status_code == 204
    assert db.session.scalar(select(func.count(ConfiguredAnswer.id)).where(ConfiguredAnswer.question_id == parent.id)) == 0
    assert db.session.scalar(select(func.count()).select_from(FollowUpQuestion)) == 0
    assert db.session.scalar(select(func.count(ConversationFlowStep.id))) == 0  # whole flow tree started here


def test_update_question(admin_client):
    q = q_by_text("How do I create an event?")
    response = admin_client.put(f"{API}/questions/{q.id}", json={
        "text": "How do I add an event?", "category_id": q.category_id, "keywords": ["add event"],
        "is_suggested": True, "display_order": 2, "is_enabled": True})
    assert response.status_code == 200
    db.session.refresh(q)
    assert q.text == "How do I add an event?" and q.is_suggested


# --- Answers -----------------------------------------------------------------------------------

def test_answer_crud_and_active_answer(admin_client):
    q = new_question(admin_client)
    second = admin_client.post(f"{API}/questions/{q['id']}/answers", json={
        "body_markdown": "Higher priority answer", "priority": 5,
        "links": [{"label": "Map", "url": "https://example.com/map"}]}).get_json()
    detail = admin_client.get(f"{API}/questions/{q['id']}").get_json()
    assert detail["answers"][0]["id"] == second["id"]
    assert "Higher priority answer" in detail["preview"]["answer_html"]
    assert detail["preview"]["links"][0]["url"] == "https://example.com/map"

    admin_client.patch(f"/api/admin/chat/answers/{second['id']}", json={"is_enabled": False})
    detail = admin_client.get(f"{API}/questions/{q['id']}").get_json()
    assert "science lab" in detail["preview"]["answer_html"]  # falls back to the other answer

    assert admin_client.delete(f"/api/admin/chat/answers/{second['id']}").status_code == 204
    assert len(admin_client.get(f"{API}/questions/{q['id']}").get_json()["answers"]) == 1


def test_equal_priority_uses_oldest_answer(admin_client):
    q = new_question(admin_client, answer="First answer")
    admin_client.post(f"{API}/questions/{q['id']}/answers", json={"body_markdown": "Second answer"})
    detail = admin_client.get(f"{API}/questions/{q['id']}").get_json()
    assert "First answer" in detail["preview"]["answer_html"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "www.example.com"])
def test_unsafe_answer_links_rejected(admin_client, url):
    q = new_question(admin_client)
    response = admin_client.post(f"{API}/questions/{q['id']}/answers", json={
        "body_markdown": "x", "links": [{"label": "Bad", "url": url}]})
    assert response.status_code == 422 and "links.0.url" in response.get_json()["fields"]


def test_template_is_applied_to_answer(admin_client):
    template = admin_client.post(f"{API}/templates", json={
        "name": "Signed", "body_markdown": "{answer}\n\n— {assistant_name}"}).get_json()
    q = new_question(admin_client)
    answer_id = q["answers"][0]["id"]
    saved = admin_client.put(f"/api/admin/chat/answers/{answer_id}", json={
        "body_markdown": "Room 12.", "template_id": template["id"]}).get_json()
    assert "Room 12." in saved["rendered_html"] and "SchoolSync Assistant" in saved["rendered_html"]

    # Deleting the template keeps the answer text.
    admin_client.delete(f"{API}/templates/{template['id']}")
    answer = db.session.get(ConfiguredAnswer, answer_id)
    assert answer.template_id is None and answer.body_markdown == "Room 12."


def test_template_must_contain_answer_placeholder(admin_client):
    response = admin_client.post(f"{API}/templates", json={"name": "Broken", "body_markdown": "Hello"})
    assert response.status_code == 422 and "{answer}" in response.get_json()["fields"]["body_markdown"]


def test_live_preview_endpoint(admin_client):
    html = admin_client.post(f"{API}/preview", json={"body_markdown": "Hi from **{assistant_name}** <b>x</b>"}).get_json()["html"]
    assert "<strong>SchoolSync Assistant</strong>" in html and "&lt;b&gt;" in html


# --- Follow-ups & suggested order ----------------------------------------------------------------

def test_set_follow_ups_in_order(admin_client):
    parent = q_by_text("How do I manage my business schedule?")
    ids = [q_by_text(t).id for t in ("How do I delete an event?", "How do I create an event?")]
    detail = admin_client.put(f"{API}/questions/{parent.id}/follow-ups", json={"question_ids": ids}).get_json()
    assert [f["id"] for f in detail["follow_ups"]] == ids
    assert [f["text"] for f in detail["preview"]["follow_ups"]] == ["How do I delete an event?", "How do I create an event?"]


def test_follow_up_rules(admin_client):
    parent = q_by_text("How do I manage my business schedule?")
    assert admin_client.put(f"{API}/questions/{parent.id}/follow-ups", json={"question_ids": [parent.id]}).status_code == 422
    assert admin_client.put(f"{API}/questions/{parent.id}/follow-ups", json={"question_ids": [99999]}).status_code == 409
    child = q_by_text("How do I edit an event?").id
    assert admin_client.put(f"{API}/questions/{parent.id}/follow-ups", json={"question_ids": [child, child]}).status_code == 422


def test_reorder_suggested(admin_client):
    items = admin_client.get(f"{API}/questions/suggested").get_json()
    reversed_ids = [q["id"] for q in reversed(items)]
    assert admin_client.put(f"{API}/questions/suggested-order", json={"ids": reversed_ids}).status_code == 200
    assert [q["id"] for q in admin_client.get(f"{API}/questions/suggested").get_json()] == reversed_ids


def test_new_suggestion_goes_last(admin_client):
    created = new_question(admin_client, is_suggested=True)
    assert created["display_order"] == 5  # four seeded suggestions are #1-#4
    q = q_by_text("How do I edit an event?")
    patched = admin_client.patch(f"{API}/questions/{q.id}", json={"is_suggested": True}).get_json()
    assert patched["display_order"] == 6


def test_markdown_list_right_after_sentence():
    html = render_markdown("Steps:\n- one\n- two")
    assert "<ul>" in html and "<li>one</li>" in html


def test_reorder_suggested_rejects_non_suggested(admin_client):
    not_suggested = q_by_text("How do I edit an event?").id
    assert admin_client.put(f"{API}/questions/suggested-order", json={"ids": [not_suggested]}).status_code == 409


# --- Flows ---------------------------------------------------------------------------------------

def tree(*ids):
    """tree(a, b, c) -> a -> b -> c (a chain)."""
    node = None
    for qid in reversed(ids):
        node = {"question_id": qid, "children": [node] if node else []}
    return node


def test_flow_create_update_and_tree(admin_client):
    a, b, c = (q_by_text(t).id for t in ("How do I manage my business schedule?", "How do I create an event?", "How do I edit an event?"))
    flow = admin_client.post(f"{API}/flows", json={"name": "Event basics", "root": tree(a, b, c)}).get_json()
    assert flow["entry_question"]["id"] == a and flow["step_count"] == 3
    assert flow["root"]["children"][0]["children"][0]["question_id"] == c

    updated = admin_client.put(f"{API}/flows/{flow['id']}", json={
        "name": "Event basics", "is_enabled": False,
        "root": {"question_id": a, "children": [{"question_id": c, "children": []}, {"question_id": b, "children": []}]}}).get_json()
    assert [n["question_id"] for n in updated["root"]["children"]] == [c, b]
    assert updated["step_count"] == 3 and updated["is_enabled"] is False


def test_flow_limits_and_missing_questions(admin_client):
    qid = q_by_text("How do I create an event?").id
    too_deep = tree(*([qid] * 7))
    response = admin_client.post(f"{API}/flows", json={"name": "Deep", "root": too_deep})
    assert response.status_code == 422 and "levels deep" in response.get_json()["fields"]["root"]
    assert admin_client.post(f"{API}/flows", json={"name": "Ghost", "root": tree(99999)}).status_code == 409
    assert admin_client.post(f"{API}/flows", json={"name": "Business schedule help"}).status_code == 422


def test_flow_delete(admin_client):
    flow_id = admin_client.get(f"{API}/flows").get_json()["items"][0]["id"]
    assert admin_client.delete(f"{API}/flows/{flow_id}").status_code == 204
    assert db.session.scalar(select(func.count(ConversationFlowStep.id))) == 0
    assert q_by_text("How do I manage my business schedule?") is not None  # questions kept


# --- Fallback --------------------------------------------------------------------------------------

def test_fallback_answer_used_by_chat(admin_client):
    admin_client.put(f"{API}/fallback", json={"fallback_answer": "Coba tanyakan dengan kata lain."})
    assert "Coba tanyakan dengan kata lain." in ask(admin_client, "zzz qqq")["html"]


def test_fallback_cannot_be_empty(admin_client):
    assert admin_client.put(f"{API}/fallback", json={"fallback_answer": "   "}).status_code == 422


# --- Permissions & audit -----------------------------------------------------------------------------

def test_viewer_can_read_but_not_write(client, make_user):
    login(client, make_user("viewer").email)
    assert client.get(f"{API}/questions").status_code == 200
    assert client.get("/admin/chat/questions").status_code == 200
    assert client.post(f"{API}/categories", json={"name": "Nope"}).status_code == 403
    assert client.put(f"{API}/fallback", json={"fallback_answer": "x"}).status_code == 403
    assert client.get("/admin/chat/flows/new").status_code == 403
    assert b'id="new-question"' not in client.get("/admin/chat/questions").data


def test_content_manager_can_write(client, make_user):
    login(client, make_user("content_manager").email)
    assert client.post(f"{API}/categories", json={"name": "Allowed"}).status_code == 201


def test_changes_are_audited_with_before_and_after(admin_client):
    q = q_by_text("How do I create an event?")
    admin_client.patch(f"{API}/questions/{q.id}", json={"is_suggested": True})
    log = db.session.scalar(select(ActivityLog).where(ActivityLog.entity_type == "configured_questions")
                            .order_by(ActivityLog.id.desc()))
    assert log.action == "update" and log.before["is_suggested"] is False and log.after["is_suggested"] is True
    assert log.actor_id is not None


# --- Pages --------------------------------------------------------------------------------------------

def test_editor_pages(admin_client):
    q = q_by_text("How do I create an event?")
    page = admin_client.get(f"/admin/chat/questions/{q.id}")
    assert page.status_code == 200 and b"How do I create an event?" in page.data
    assert b'aria-current="page"' in page.data  # "Questions & answers" stays highlighted
    assert admin_client.get("/admin/chat/questions/99999").status_code == 404
    assert admin_client.get("/admin/chat/flows/new").status_code == 200
    assert admin_client.get("/admin/chat/flows/99999").status_code == 404
