"""Step 12: conversation monitoring (history, needs review, analytics) and the AI connection test."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import ActivityLog, AssistantSettings, ChatMessage, ChatSession, ConfiguredQuestion
from app.models.base import utcnow
from app.services import ai_answer

from .conftest import ask, login

API = "/api/admin/conversations"
MATH = "Kapan ujian Matematika kelas X?"


@pytest.fixture()
def student(make_user):
    return make_user("user", full_name="Siti Student")


def chat_as(app, user, *messages):
    """Send messages as `user` in one conversation; returns its public id."""
    client = app.test_client()
    login(client, user.email)
    session_id = None
    for text in messages:
        data = client.post("/api/chat/messages", json={"message": text, "session_id": session_id}).get_json()
        session_id = data["session_id"]
    return session_id


def unanswered_ids():
    return [m.id for m in db.session.scalars(select(ChatMessage).where(ChatMessage.resolved_by == "fallback"))]


# --- Pages & permissions ----------------------------------------------------------------------

def test_pages_render(app, admin_client, student):
    session_id = chat_as(app, student, MATH)
    for path in ("/admin/conversations", "/admin/conversations?tab=all", "/admin/conversations?tab=review",
                 f"/admin/conversations/{session_id}"):
        assert admin_client.get(path).status_code == 200
    assert admin_client.get("/admin/conversations/not-a-real-id").status_code == 404


def test_viewer_can_read_but_not_review(app, client, make_user, student):
    chat_as(app, student, "zzz qqq")
    login(client, make_user("viewer").email)
    assert client.get(API).status_code == 200
    assert client.post(f"{API}/review", json={"ids": unanswered_ids()}).status_code == 403


def test_students_cannot_monitor(app, user_client):
    assert user_client.get(API).status_code == 403
    assert user_client.get("/admin/conversations").status_code == 403


# --- List & detail ------------------------------------------------------------------------------

def test_list_shows_conversations_with_counts(app, admin_client, student):
    session_id = chat_as(app, student, MATH, "zzz qqq")
    data = admin_client.get(API).get_json()
    row = next(r for r in data["items"] if r["id"] == session_id)
    assert row["title"] == MATH and row["questions"] == 2 and row["open_reviews"] == 1
    assert row["user"]["name"] == "Siti Student"


def test_list_filters_and_search(app, admin_client, student, make_user):
    answered = chat_as(app, student, MATH)
    unanswered = chat_as(app, make_user("user", full_name="Budi"), "zzz qqq")
    ids = lambda **params: [r["id"] for r in admin_client.get(API, query_string=params).get_json()["items"]]
    assert ids(status="needs_review") == [unanswered]
    assert ids(source="configured") == [answered]
    assert ids(q="Budi") == [unanswered]
    assert ids(q="matematika") == [answered]
    assert set(ids()) == {answered, unanswered}
    assert admin_client.get(API, query_string={"source": "magic"}).status_code == 422
    assert admin_client.get(API, query_string={"from": "2026-13-01"}).status_code == 422


def test_detail_shows_how_each_answer_was_chosen(app, admin_client, student):
    session_id = chat_as(app, student, MATH, "zzz qqq")
    data = admin_client.get(f"{API}/{session_id}").get_json()
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    configured, fallback = data["messages"][1], data["messages"][3]
    assert configured["resolved_by"] == "configured" and configured["question"]["text"] == MATH
    assert fallback["resolved_by"] == "fallback" and fallback["needs_review"] and fallback["reviewed_at"] is None
    assert admin_client.get(f"{API}/nope").status_code == 404


# --- Needs review ----------------------------------------------------------------------------------

def test_review_queue_and_marking(app, admin_client, student):
    chat_as(app, student, "berapa biaya spp?", MATH)
    queue = admin_client.get(f"{API}/review").get_json()
    assert queue["total"] == 1 and queue["open_total"] == 1
    item = queue["items"][0]
    assert item["question"] == "berapa biaya spp?" and item["user"]["name"] == "Siti Student"

    assert admin_client.post(f"{API}/review", json={"ids": [item["id"]]}).get_json() == {"updated": 1}
    assert admin_client.get(f"{API}/review").get_json()["total"] == 0
    reviewed = admin_client.get(f"{API}/review", query_string={"status": "reviewed"}).get_json()
    assert reviewed["items"][0]["reviewed_at"] is not None
    log = db.session.scalar(select(ActivityLog).where(ActivityLog.entity_type == "chat_messages"))
    assert "reviewed" in log.summary

    admin_client.post(f"{API}/review", json={"ids": [item["id"]], "reviewed": False})
    assert admin_client.get(f"{API}/review").get_json()["open_total"] == 1


def test_review_only_touches_flagged_replies(app, admin_client, student):
    chat_as(app, student, MATH)
    answered = db.session.scalar(select(ChatMessage).where(ChatMessage.resolved_by == "configured"))
    assert admin_client.post(f"{API}/review", json={"ids": [answered.id]}).status_code == 404
    assert admin_client.post(f"{API}/review", json={"ids": []}).status_code == 422


def test_answering_an_unanswered_question_fixes_the_chat(app, admin_client, student):
    chat_as(app, student, "berapa biaya spp?")
    category_id = db.session.scalar(select(ConfiguredQuestion.category_id).limit(1))
    created = admin_client.post("/api/admin/chat/questions", json={
        "text": "Berapa biaya SPP?", "category_id": category_id, "keywords": ["biaya spp"],
        "answer": "SPP adalah **Rp 500.000** per bulan.", "is_enabled": True, "is_suggested": False, "display_order": 0})
    assert created.status_code == 201
    reply = ask(_client_for(app, student), "berapa biaya spp?")
    assert reply["resolved_by"] == "configured" and "Rp 500.000" in reply["html"]


def _client_for(app, user):
    client = app.test_client()
    login(client, user.email)
    return client


# --- Stats ----------------------------------------------------------------------------------------------

def test_stats(app, admin_client, student, make_user):
    chat_as(app, student, MATH, "Berapa biaya SPP?", "berapa biaya spp")
    chat_as(app, make_user("user"), "kapan ujian matematika kelas x")
    old = ChatSession(user_id=student.id, started_at=utcnow() - timedelta(days=60), last_message_at=utcnow() - timedelta(days=60))
    db.session.add(old)
    db.session.flush()
    db.session.add(ChatMessage(session_id=old.id, role="user", content="old question", created_at=utcnow() - timedelta(days=60)))
    db.session.commit()

    data = admin_client.get(f"{API}/stats?days=30").get_json()
    t = data["totals"]
    assert t["conversations"] == 2 and t["questions"] == 4 and t["people"] == 2
    assert t["answered_rate"] == 0.5 and t["open_reviews"] == 2
    assert {s["source"]: s["count"] for s in data["sources"]}["fallback"] == 2
    assert data["frequent_questions"][0]["count"] == 2  # MATH twice (case/punctuation ignored)
    assert data["frequent_unanswered"] == [{"text": "Berapa biaya SPP?", "count": 2}]
    assert data["top_configured"][0] == {"id": db.session.scalar(select(ConfiguredQuestion.id).where(ConfiguredQuestion.text == MATH)),
                                         "text": MATH, "count": 2}
    assert len(data["daily"]) == 30 and sum(d["fallback"] for d in data["daily"]) == 2
    assert "old question" not in str(admin_client.get(f"{API}/stats?days=30").get_json()["frequent_questions"])
    assert admin_client.get(f"{API}/stats?days=90").get_json()["totals"]["questions"] == 5


# --- AI connection test ------------------------------------------------------------------------------------

def test_ai_test_without_key(admin_client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    data = admin_client.post("/api/admin/assistant/test").get_json()
    assert data["ok"] is False and "ANTHROPIC_API_KEY" in data["message"]


class FakeClient:
    class APIError(Exception):
        def __init__(self, status_code):
            self.status_code = status_code

    def __init__(self, error=None):
        self.error, self.request = error, None
        self.messages = self

    def create(self, **request):
        self.request = request
        if self.error:
            raise self.error
        return SimpleNamespace(model=request["model"])


def test_ai_test_success_and_failure(admin_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    fake = FakeClient()
    monkeypatch.setattr(ai_answer, "_client", lambda settings: (fake, fake))
    data = admin_client.post("/api/admin/assistant/test").get_json()
    assert data["ok"] and AssistantSettings.get().ai_model in data["message"]
    assert fake.request["output_config"] == {"effort": "low"} and "temperature" not in fake.request
    assert "test-key-not-real" not in str(data)

    failing = FakeClient(error=FakeClient.APIError(401))
    monkeypatch.setattr(ai_answer, "_client", lambda settings: (failing, failing))
    data = admin_client.post("/api/admin/assistant/test").get_json()
    assert data["ok"] is False and "rejected" in data["message"]
    assert db.session.scalar(select(ActivityLog).where(ActivityLog.action == "ai_test")) is not None


def test_ai_test_needs_write_permission(client, make_user):
    login(client, make_user("content_manager").email)
    assert client.post("/api/admin/assistant/test").status_code == 403
