"""Step 11: AI Chat engine (configured Q&A -> knowledge -> AI -> fallback), chat API,
conversation history, and the student pages."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import (
    AssistantSettings,
    ChatMessage,
    ChatSession,
    ConfiguredAnswer,
    ConfiguredQuestion,
    Event,
    KnowledgeEntry,
    QuestionCategory,
)
from app.models.base import utcnow
from app.services import ai_answer
from app.services.question_match import score_question

from .conftest import ask, login

MATH = "Kapan ujian Matematika kelas X?"


def question(text):
    return db.session.scalar(select(ConfiguredQuestion).where(ConfiguredQuestion.text == text))


def set_features(**values):
    settings = AssistantSettings.get()
    settings.features = {**(settings.features or {}), **values}
    db.session.commit()


def disable_questions():
    for q in db.session.scalars(select(ConfiguredQuestion)):
        q.is_enabled = False
    db.session.commit()


def enable_ai(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    settings = AssistantSettings.get()
    settings.ai_enabled = True
    db.session.commit()


# --- Config & suggested questions ----------------------------------------------------------

def test_config_comes_from_assistant_settings(user_client):
    settings = AssistantSettings.get()
    settings.assistant_name = "Bu Sari"
    settings.welcome_message = "Selamat datang!"
    db.session.commit()
    data = user_client.get("/api/chat/config").get_json()
    assert data["assistant"]["name"] == "Bu Sari" and data["assistant"]["welcome_message"] == "Selamat datang!"
    assert MATH in [q["text"] for q in data["suggested"]]


def test_suggested_hides_disabled_and_unanswered(user_client):
    math = question(MATH)
    math.is_enabled = False
    office = question("Jam berapa kantor administrasi buka?")
    for answer in office.answers:
        answer.is_enabled = False
    db.session.commit()
    texts = [q["text"] for q in user_client.get("/api/chat/config").get_json()["suggested"]]
    assert MATH not in texts and office.text not in texts and texts


def test_suggested_questions_can_be_turned_off(user_client):
    set_features(suggested_questions=False)
    assert user_client.get("/api/chat/config").get_json()["suggested"] == []


# --- Matching -------------------------------------------------------------------------------

def test_matcher_scores(app):
    math = question(MATH)
    assert score_question(MATH.lower() + "  ", math)[0] == 1.0
    assert score_question("info ujian matematika dong", math)[1] == ["ujian matematika"]
    assert score_question("berapa biaya spp", math)[0] < 0.6


def test_configured_answer_wins(user_client):
    reply = ask(user_client, "kapan ujian matematika kelas x")
    assert reply["resolved_by"] == "configured" and "12 No" in reply["html"]
    stored = db.session.get(ChatMessage, reply["id"])
    assert stored.matched_question_id == question(MATH).id and stored.confidence >= 0.6 and not stored.needs_review


def test_clicked_suggestion_uses_that_question(user_client):
    target = question("How do I create an event?")
    reply = ask(user_client, "anything at all", question_id=target.id)
    assert reply["resolved_by"] == "configured" and db.session.get(ChatMessage, reply["id"]).confidence == 1.0


def test_clicked_disabled_question_is_not_answered(user_client):
    target = question("How do I create an event?")
    target.is_enabled = False
    db.session.commit()
    reply = ask(user_client, "zzz qqq", question_id=target.id)
    assert reply["resolved_by"] == "fallback"


def test_follow_ups_and_links(user_client):
    parent = question("How do I manage my business schedule?")
    parent.active_answer.links = [{"label": "Events", "url": "/admin/events"}, {"label": "Bad", "url": "javascript:alert(1)"}]
    db.session.commit()
    reply = ask(user_client, parent.text)
    assert [f["text"] for f in reply["follow_ups"]] == [q.text for q in parent.follow_ups]
    assert reply["links"] == [{"label": "Events", "url": "/admin/events", "external": False}]

    set_features(follow_up_buttons=False, show_sources=False)
    reply = ask(user_client, parent.text)
    assert reply["follow_ups"] == [] and reply["links"] == []


def test_threshold_controls_matching(user_client):
    settings = AssistantSettings.get()
    settings.match_threshold = 1.0
    db.session.commit()
    assert ask(user_client, "info ujian matematika dong")["resolved_by"] != "configured"
    settings.match_threshold = 0.6
    db.session.commit()
    assert ask(user_client, "info ujian matematika dong")["resolved_by"] == "configured"


def test_answer_template_and_placeholder_are_applied(user_client):
    q = question("How do I create an event?")
    q.active_answer.body_markdown = "Hi, I'm {assistant_name}. Use **Events**."
    AssistantSettings.get().assistant_name = "Pak Budi"
    db.session.commit()
    assert "Pak Budi" in ask(user_client, q.text)["html"]


# --- Knowledge & fallback ------------------------------------------------------------------------

def test_knowledge_answers_when_no_question_matches(user_client):
    disable_questions()
    reply = ask(user_client, MATH)
    entry = db.session.scalar(select(KnowledgeEntry).where(KnowledgeEntry.external_ref == "SCH001"))
    assert reply["resolved_by"] == "knowledge" and reply["source"] == {"type": "knowledge", "title": entry.title}
    assert db.session.get(ChatMessage, reply["id"]).matched_knowledge_id == entry.id


def test_knowledge_can_be_turned_off(user_client):
    disable_questions()
    set_features(use_knowledge_base=False)
    reply = ask(user_client, MATH)
    assert reply["resolved_by"] == "fallback" and reply["needs_review"] is True


def test_fallback_is_flagged_for_review(user_client):
    reply = ask(user_client, "zzz qqq")
    stored = db.session.get(ChatMessage, reply["id"])
    assert stored.resolved_by == "fallback" and stored.needs_review


def test_user_text_and_answers_are_escaped(user_client):
    response = user_client.post("/api/chat/messages", json={"message": "<img src=x onerror=alert(1)>"})
    data = response.get_json()
    assert data["user_message"]["text"] == "<img src=x onerror=alert(1)>"  # returned as text, shown with textContent
    q = question("How do I create an event?")
    q.active_answer.body_markdown = "Hello <script>alert(1)</script> [x](javascript:alert(1))"
    db.session.commit()
    html = ask(user_client, q.text)["html"]
    assert "<script>" not in html and "javascript:" not in html


def test_message_is_validated(user_client):
    assert user_client.post("/api/chat/messages", json={"message": "   "}).status_code == 422
    assert user_client.post("/api/chat/messages", json={"message": "x" * 501}).status_code == 422
    assert user_client.post("/api/chat/messages", json={"message": "hi", "extra": 1}).status_code == 422


# --- AI ----------------------------------------------------------------------------------------------

def test_ai_answers_when_nothing_else_fits(user_client, monkeypatch):
    enable_ai(monkeypatch)
    calls = []

    def fake(settings, system_prompt, history, message):
        calls.append((system_prompt, history, message))
        return "Perpustakaan buka **07.00–15.00**."

    monkeypatch.setattr(ai_answer, "generate_answer", fake)
    reply = ask(user_client, "zzz qqq")
    assert reply["resolved_by"] == "ai" and "<strong>07.00–15.00</strong>" in reply["html"]
    assert reply["source"]["type"] == "ai" and reply["needs_review"] is False
    assert calls and calls[0][2] == "zzz qqq"


def test_ai_is_not_used_without_key(user_client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    AssistantSettings.get().ai_enabled = True
    db.session.commit()
    monkeypatch.setattr(ai_answer, "generate_answer", lambda *a: pytest.fail("AI must not be called"))
    assert ask(user_client, "zzz qqq")["resolved_by"] == "fallback"


@pytest.mark.parametrize("outcome", ["no_answer", "error", "crash"])
def test_ai_failures_fall_back(user_client, monkeypatch, outcome):
    enable_ai(monkeypatch)

    def fake(*args):
        if outcome == "error":
            raise ai_answer.AIUnavailable("provider down")
        if outcome == "crash":
            raise RuntimeError("boom")
        return None

    monkeypatch.setattr(ai_answer, "generate_answer", fake)
    reply = ask(user_client, "zzz qqq")
    assert reply["resolved_by"] == "fallback" and reply["needs_review"] is True


def test_ai_gets_history_and_school_information(user_client, monkeypatch):
    enable_ai(monkeypatch)
    db.session.add(Event(title="Pentas Seni", start_at=utcnow() + timedelta(days=3), category="activity", status="upcoming"))
    settings = AssistantSettings.get()
    settings.personality = "Sabar dan ramah."
    db.session.commit()
    seen = {}

    def fake(settings, system_prompt, history, message):
        seen.update(prompt=system_prompt, history=history)
        return "Jawaban."

    monkeypatch.setattr(ai_answer, "generate_answer", fake)
    first = user_client.post("/api/chat/messages", json={"message": "zzz qqq"}).get_json()
    ask(user_client, "dan yang lain?", session_id=first["session_id"])
    assert seen["history"] == [{"role": "user", "content": "zzz qqq"}, {"role": "assistant", "content": "Jawaban."}]
    assert "Pentas Seni" in seen["prompt"] and "Sabar dan ramah." in seen["prompt"] and ai_answer.NO_ANSWER in seen["prompt"]
    assert "test-key-not-real" not in seen["prompt"]


class FakeAnthropic:
    """Stands in for the anthropic module: records the request, returns a canned response."""

    class APIError(Exception):
        pass

    def __init__(self, response=None, error=None):
        self.response, self.error, self.request = response, error, None
        self.messages = self

    def stream(self, **request):
        self.request = request
        fake = self

        class Stream:
            def __enter__(self):
                if fake.error:
                    raise fake.error
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return fake.response

        return Stream()


def _response(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[
        SimpleNamespace(type="thinking", thinking="..."), SimpleNamespace(type="text", text=text)])


@pytest.mark.parametrize("text, stop, expected", [
    ("Jawabannya **ini**.", "end_turn", "Jawabannya **ini**."),
    ("NO_ANSWER", "end_turn", None),
    ("", "refusal", None),
])
def test_generate_answer_parses_the_response(app, monkeypatch, text, stop, expected):
    enable_ai(monkeypatch)
    fake = FakeAnthropic(_response(text, stop))
    monkeypatch.setattr(ai_answer, "_client", lambda settings: (fake, fake))
    settings = AssistantSettings.get()
    assert ai_answer.generate_answer(settings, "system", [], "pertanyaan") == expected
    request = fake.request
    assert request["model"] == settings.ai_model and request["max_tokens"] == settings.max_tokens
    assert request["output_config"] == {"effort": settings.ai_effort}
    assert "temperature" not in request and request["messages"][-1] == {"role": "user", "content": "pertanyaan"}


def test_generate_answer_wraps_api_errors(app, monkeypatch):
    enable_ai(monkeypatch)
    fake = FakeAnthropic(error=FakeAnthropic.APIError("rate limited"))
    monkeypatch.setattr(ai_answer, "_client", lambda settings: (fake, fake))
    with pytest.raises(ai_answer.AIUnavailable):
        ai_answer.generate_answer(AssistantSettings.get(), "system", [], "q")


# --- Sessions & history ---------------------------------------------------------------------------------

def test_conversation_is_stored_and_continued(user_client):
    first = user_client.post("/api/chat/messages", json={"message": MATH}).get_json()
    second = user_client.post("/api/chat/messages", json={"message": "zzz qqq", "session_id": first["session_id"]}).get_json()
    assert first["session_id"] == second["session_id"] and second["title"] == MATH
    session = db.session.scalar(select(ChatSession).where(ChatSession.public_id == first["session_id"]))
    assert len(session.messages) == 4 and session.last_message_at is not None

    listed = user_client.get("/api/chat/sessions").get_json()["items"]
    assert listed[0]["id"] == first["session_id"] and listed[0]["messages"] == 4
    detail = user_client.get(f"/api/chat/sessions/{first['session_id']}").get_json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]


def test_users_cannot_see_or_continue_others_conversations(client, make_user):
    login(client, make_user("user").email)
    session_id = client.post("/api/chat/messages", json={"message": "hi"}).get_json()["session_id"]
    client.post("/auth/logout")
    login(client, make_user("user").email)
    assert client.get(f"/api/chat/sessions/{session_id}").status_code == 404
    assert client.post("/api/chat/messages", json={"message": "hi", "session_id": session_id}).status_code == 404
    assert client.get("/api/chat/sessions").get_json()["items"] == []


def test_history_can_be_turned_off(user_client):
    session_id = user_client.post("/api/chat/messages", json={"message": "hi"}).get_json()["session_id"]
    set_features(chat_history=False)
    assert user_client.get("/api/chat/sessions").get_json() == {"items": [], "enabled": False}
    assert user_client.get(f"/api/chat/sessions/{session_id}").status_code == 404


def test_chat_api_requires_login(client):
    assert client.get("/api/chat/config").status_code == 401
    assert client.get("/api/chat/sessions").status_code == 401


# --- Student pages & events --------------------------------------------------------------------------------

def test_student_events_api(user_client):
    db.session.add_all([
        Event(title="Soon", start_at=utcnow() + timedelta(days=2), category="exam", status="upcoming"),
        Event(title="Cancelled", start_at=utcnow() + timedelta(days=2), category="exam", status="cancelled"),
        Event(title="Far", start_at=utcnow() + timedelta(days=60), category="exam", status="upcoming"),
    ])
    db.session.commit()
    titles = [e["title"] for e in user_client.get("/api/me/events").get_json()["items"]]
    assert "Soon" in titles and "Cancelled" not in titles and "Far" not in titles


def test_student_pages_use_branding_and_no_sample_data(user_client):
    from app.models import SystemSetting
    db.session.get(SystemSetting, "app.name").value = "Sekolah Pintar"
    db.session.commit()
    for path in ("/dashboard", "/chatbot"):
        html = user_client.get(path).data.decode()
        assert "Sekolah Pintar" in html
        assert "simulated" not in html.lower() and "Prototype" not in html
    assert "Sekolah Pintar" in user_client.get("/dashboard").data.decode()


def test_sign_in_page_uses_branding(client):
    from app.models import SystemSetting
    db.session.get(SystemSetting, "app.name").value = "Sekolah Pintar"
    db.session.commit()
    assert b"Sekolah Pintar" in client.get("/auth/login").data
