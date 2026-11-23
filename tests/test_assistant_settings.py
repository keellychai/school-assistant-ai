"""Step 5: Assistant settings (identity, behaviour, features, AI provider, avatar)."""

import io

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import ActivityLog, AssistantSettings

from .conftest import login

API = "/api/admin/assistant"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def current(client):
    return client.get(API).get_json()


def valid_body(**overrides):
    body = {
        "assistant_name": "Asisten Sekolah",
        "welcome_message": "Halo! Ada yang bisa saya bantu?",
        "initial_greeting": "Tanya soal jadwal, pengumuman, atau administrasi.",
        "default_language": "id",
        "tone": "friendly",
        "personality": "Sabar dan jelas.",
        "fallback_answer": "Maaf, saya belum tahu jawabannya.",
        "match_threshold": 0.55,
        "features": {"suggested_questions": True, "follow_up_buttons": False, "show_sources": True,
                     "chat_history": True, "use_knowledge_base": True, "ai_use_knowledge_context": True,
                     "ai_history_messages": 6},
        "ai_enabled": True,
        "ai_provider": "anthropic",
        "ai_model": "claude-sonnet-5-5",
        "api_key_env_var": "ANTHROPIC_API_KEY",
        "ai_effort": "low",
        "max_tokens": 8000,
    }
    body.update(overrides)
    return body


# --- Reading -------------------------------------------------------------------------

def test_defaults_and_options(admin_client):
    data = current(admin_client)
    s = data["settings"]
    assert s["ai_provider"] == "anthropic" and s["ai_model"] == "claude-opus-5-5"
    assert s["ai_effort"] == "medium" and s["max_tokens"] == 16000
    assert s["features"]["suggested_questions"] is True and s["features"]["ai_history_messages"] == 10
    model_ids = [m["id"] for m in data["options"]["providers"][0]["models"]]
    assert model_ids[0] == "claude-opus-5-5"
    assert "temperature" not in s
    assert data["status"]["state"] == "off"


def test_suggested_preview_lists_what_chat_shows(admin_client):
    preview = current(admin_client)["suggested_preview"]
    assert preview[0] == "Kapan ujian Matematika kelas X?" and len(preview) == 4


# --- Saving ---------------------------------------------------------------------------

def test_save_persists_and_is_audited(admin_client):
    result = admin_client.put(API, json=valid_body()).get_json()
    assert "ai_model" in result["changed"] and "assistant_name" in result["changed"]
    db.session.expire_all()
    settings = AssistantSettings.get()
    assert settings.assistant_name == "Asisten Sekolah" and settings.ai_effort == "low"
    assert settings.features["follow_up_buttons"] is False and settings.features["ai_history_messages"] == 6
    log = db.session.scalar(select(ActivityLog).where(ActivityLog.entity_type == "assistant_settings")
                            .order_by(ActivityLog.id.desc()))
    assert log.before["ai_model"] == "claude-opus-5-5" and log.after["ai_model"] == "claude-sonnet-5-5"
    assert current(admin_client)["settings"]["updated_by"] is not None


def test_saving_unchanged_settings_logs_nothing(admin_client):
    admin_client.put(API, json=valid_body())
    count = db.session.query(ActivityLog).count()
    result = admin_client.put(API, json=valid_body()).get_json()
    assert result["changed"] == [] and db.session.query(ActivityLog).count() == count


def test_custom_model_id_allowed(admin_client):
    assert admin_client.put(API, json=valid_body(ai_model="claude-opus-6")).status_code == 200


@pytest.mark.parametrize("field,value", [
    ("tone", "sarcastic"),
    ("default_language", "fr"),
    ("ai_effort", "turbo"),
    ("ai_provider", "someone-else"),
    ("ai_model", "gpt-4o"),
    ("max_tokens", 100),
    ("match_threshold", 1.5),
    ("assistant_name", " "),
    ("welcome_message", ""),
    ("fallback_answer", ""),
])
def test_validation(admin_client, field, value):
    response = admin_client.put(API, json=valid_body(**{field: value}))
    assert response.status_code == 422 and field in response.get_json()["fields"]


def test_temperature_is_rejected(admin_client):
    response = admin_client.put(API, json=valid_body(temperature=0.7))
    assert response.status_code == 422 and "temperature" in response.get_json()["fields"]


def test_unknown_feature_rejected(admin_client):
    body = valid_body()
    body["features"]["voice_mode"] = True
    assert admin_client.put(API, json=body).status_code == 422


# --- API key handling ---------------------------------------------------------------------

@pytest.mark.parametrize("name", ["SECRET_KEY", "DATABASE_URL", "ADMIN_PASSWORD", "anthropic_api_key", "PATH"])
def test_key_variable_must_look_like_an_api_key(admin_client, name):
    response = admin_client.put(API, json=valid_body(api_key_env_var=name))
    assert response.status_code == 422 and "api_key_env_var" in response.get_json()["fields"]


def test_status_reports_key_presence_but_never_the_value(admin_client, monkeypatch):
    admin_client.put(API, json=valid_body(api_key_env_var="SCHOOL_TEST_API_KEY"))
    monkeypatch.delenv("SCHOOL_TEST_API_KEY", raising=False)
    assert admin_client.get(f"{API}/status").get_json()["state"] == "missing_key"

    monkeypatch.setenv("SCHOOL_TEST_API_KEY", "sk-test-very-secret-value")
    response = admin_client.get(API)
    assert response.get_json()["status"]["state"] == "ready"
    assert b"sk-test-very-secret-value" not in response.data


def test_status_off_when_ai_disabled(admin_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    admin_client.put(API, json=valid_body(ai_enabled=False))
    assert admin_client.get(f"{API}/status").get_json()["state"] == "off"


# --- Avatar upload ----------------------------------------------------------------------------

def upload(client, data, name="avatar.png"):
    return client.post(f"{API}/avatar", data={"avatar": (io.BytesIO(data), name)}, content_type="multipart/form-data")


def test_avatar_upload_serve_replace_remove(app, admin_client):
    first = upload(admin_client, PNG).get_json()["avatar_url"]
    assert first.startswith("/media/avatars/") and first.endswith(".png")
    served = admin_client.get(first)
    assert served.status_code == 200 and served.data == PNG
    assert served.headers["X-Content-Type-Options"] == "nosniff"
    served.close()  # release the file handle (Windows won't delete an open file)

    second = upload(admin_client, b"\xff\xd8\xff\xe0" + b"\x00" * 32, "photo.jpeg").get_json()["avatar_url"]
    assert second.endswith(".jpg")
    assert admin_client.get(first).status_code == 404  # old file deleted

    assert admin_client.delete(f"{API}/avatar").status_code == 204
    assert admin_client.get(second).status_code == 404
    assert AssistantSettings.get().avatar_url is None


def test_replacing_avatar_survives_locked_old_file(app, admin_client):
    first = upload(admin_client, PNG).get_json()["avatar_url"]
    held = admin_client.get(first)  # keep the old file open, like a download in progress
    response = upload(admin_client, PNG)
    held.close()
    assert response.status_code == 201
    assert AssistantSettings.get().avatar_url == response.get_json()["avatar_url"]


@pytest.mark.parametrize("data,name", [
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "evil.svg"),
    (b"just text pretending", "fake.png"),
    (b"", "empty.png"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 10), "huge.png"),
])
def test_bad_avatar_rejected(admin_client, data, name):
    response = upload(admin_client, data, name)
    assert response.status_code in (413, 422)
    assert AssistantSettings.get().avatar_url is None


def test_avatar_upload_without_file(admin_client):
    response = admin_client.post(f"{API}/avatar", data={}, content_type="multipart/form-data")
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/media/avatars/../../.env", "/media/avatars/x.png", "/media/knowledge/abc.png",
                                  "/media/avatars/" + "a" * 32 + ".svg"])
def test_media_route_rejects_anything_unexpected(client, path):
    assert client.get(path).status_code == 404


# --- Permissions, integration and page ---------------------------------------------------------

def test_content_manager_can_view_not_edit(client, make_user):
    login(client, make_user("content_manager").email)
    assert client.get(API).status_code == 200
    assert client.put(API, json=valid_body()).status_code == 403
    assert upload(client, PNG).status_code == 403
    assert client.get("/admin/assistant").status_code == 200


def test_fallback_from_settings_used_by_chat(admin_client):
    admin_client.put(API, json=valid_body(fallback_answer="Silakan tanya dengan kata lain."))
    assert admin_client.post("/api/ask", json={"question": "zzz qqq"}).get_json()["answer"] == "Silakan tanya dengan kata lain."


def test_page_renders(admin_client):
    page = admin_client.get("/admin/assistant")
    assert page.status_code == 200 and b"Assistant settings" in page.data and b"Soon" not in page.data.split(b"Assistant settings")[0][-300:]
