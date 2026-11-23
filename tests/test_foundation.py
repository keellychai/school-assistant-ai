"""Step 1 checks: schema constraints, seeding, RBAC definitions and legacy pages."""

from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    ActivityLog,
    AssistantSettings,
    ConfiguredQuestion,
    Event,
    FollowUpQuestion,
    KnowledgeEntry,
    QuestionCategory,
    Role,
    User,
)
from app.seeds import create_admin, seed_all


def count(model):
    return db.session.scalar(select(func.count()).select_from(model))


# --- Seeding -------------------------------------------------------------

def test_seed_is_idempotent(app):
    before = {m: count(m) for m in (KnowledgeEntry, Event, ConfiguredQuestion, Role)}
    seed_all()
    assert {m: count(m) for m in before} == before


def test_seed_does_not_overwrite_admin_edits(app):
    entry = db.session.scalar(select(KnowledgeEntry).where(KnowledgeEntry.external_ref == "SCH001"))
    entry.title = "Edited by admin"
    db.session.commit()
    seed_all()
    assert db.session.get(KnowledgeEntry, entry.id).title == "Edited by admin"


def test_seeded_follow_ups_and_flow(app):
    q = db.session.scalar(select(ConfiguredQuestion).where(
        ConfiguredQuestion.text == "How do I manage my business schedule?"))
    assert q.active_answer.body_markdown.startswith("You can manage")
    assert [c.text for c in q.follow_ups] == [
        "How do I create an event?", "How do I edit an event?", "How do I delete an event?"]


# --- RBAC ------------------------------------------------------------------

def test_role_permissions(app):
    user, created = create_admin("Editor@Example.com ", "longpassword", role_name="content_manager")
    assert created and user.email == "editor@example.com"
    assert user.has_permission("chatconfig.write")
    assert not user.has_permission("users.write")
    assert not user.has_permission("roles.manage")
    assert user.can_access_admin

    user.is_active = False
    assert not user.has_permission("chatconfig.write")


def test_regular_user_has_no_admin_access(app):
    user, _ = create_admin("student@example.com", "longpassword", role_name="user")
    assert not user.can_access_admin


def test_password_hashing(app):
    user, _ = create_admin("a@example.com", "correct horse battery", role_name="admin")
    assert "correct horse" not in user.password_hash
    assert user.check_password("correct horse battery")
    assert not user.check_password("wrong")
    with pytest.raises(ValueError):
        user.set_password("short")


# --- Constraints -----------------------------------------------------------

def test_event_status_must_be_valid(app):
    db.session.add(Event(title="X", start_at=datetime(2026, 12, 1), status="maybe"))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_event_end_must_not_precede_start(app):
    db.session.add(Event(title="X", start_at=datetime(2026, 12, 2), end_at=datetime(2026, 12, 1)))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_question_cannot_follow_itself(app):
    q = db.session.scalar(select(ConfiguredQuestion).limit(1))
    db.session.add(FollowUpQuestion(parent_question_id=q.id, child_question_id=q.id))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_category_with_questions_cannot_be_deleted(app):
    category = db.session.scalar(select(QuestionCategory).where(QuestionCategory.slug == "business"))
    db.session.delete(category)
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_deleting_question_cascades_answers_and_follow_ups(app):
    q = db.session.scalar(select(ConfiguredQuestion).where(
        ConfiguredQuestion.text == "How do I manage my business schedule?"))
    db.session.delete(q)
    db.session.commit()
    assert count(FollowUpQuestion) == 0


def test_assistant_settings_is_single_row(app):
    db.session.add(AssistantSettings(id=2))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_duplicate_email_rejected(app):
    role = db.session.scalar(select(Role).where(Role.name == "user"))
    for _ in range(2):
        u = User(email="dup@example.com", full_name="Dup", role=role)
        u.set_password("longpassword")
        db.session.add(u)
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_deleting_user_keeps_activity_log(app):
    user, _ = create_admin("temp@example.com", "longpassword", role_name="admin")
    db.session.add(ActivityLog(actor_id=user.id, action="login"))
    db.session.commit()
    db.session.delete(user)
    db.session.commit()
    log = db.session.scalar(select(ActivityLog).where(ActivityLog.action == "login"))
    assert log is not None and log.actor_id is None


# --- Existing pages keep working ------------------------------------------

@pytest.mark.parametrize("path", ["/", "/dashboard", "/chatbot"])
def test_pages_render(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert b'name="csrf-token"' in response.data


def test_ask_answers_from_database(client):
    response = client.post("/api/ask", json={"question": "Kapan ujian matematika kelas X?"})
    data = response.get_json()
    assert response.status_code == 200
    assert data["source"]["id"] == "SCH001"
    assert data["needs_review"] is False


def test_ask_reflects_database_changes(app, client):
    entry = db.session.scalar(select(KnowledgeEntry).where(KnowledgeEntry.external_ref == "SCH001"))
    entry.content_markdown = "Ujian Matematika dipindah ke 15 Desember 2026."
    db.session.commit()
    data = client.post("/api/ask", json={"question": "Kapan ujian matematika?"}).get_json()
    assert "15 Desember" in data["answer"]


def test_ask_unknown_question_uses_configured_fallback(app, client):
    AssistantSettings.get().fallback_answer = "Custom fallback"
    db.session.commit()
    data = client.post("/api/ask", json={"question": "zzz qqq"}).get_json()
    assert data["answer"] == "Custom fallback" and data["needs_review"] is True


def test_ask_validates_input(client):
    assert client.post("/api/ask", json={"question": ""}).status_code == 400
    assert client.post("/api/ask", json={"question": "x" * 501}).status_code == 400
