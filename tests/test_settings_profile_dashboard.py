"""Step 10: system settings, security rules, idle session timeout, My profile and the dashboard."""

import io
import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import ActivityLog, ChatMessage, ChatSession, Event, SystemSetting, User
from app.models.base import utcnow
from app.services import system_settings as ss

from .conftest import PASSWORD, login

GENERAL = {"app_name": "Sekolah Pintar", "app_tagline": "Asisten sekolah", "theme_mode": "dark",
           "primary_color": "#117A65", "default_language": "en", "timezone": "Asia/Makassar"}
SECURITY = {"session_timeout_minutes": 30, "max_failed_logins": 4, "lockout_minutes": 10, "password_min_length": 12}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# --- System settings ------------------------------------------------------------------

def test_settings_page_and_api_return_current_values(admin_client):
    assert admin_client.get("/admin/settings").status_code == 200
    data = admin_client.get("/api/admin/settings").get_json()
    assert data["settings"]["app_name"] == "SchoolSync AI"
    assert data["settings"]["timezone"] == "Asia/Jakarta"
    assert data["can_edit"] and data["can_edit_security"]
    assert "Asia/Jakarta" in data["options"]["timezones"]
    assert data["options"]["limits"]["password_min_length"] == {"min": 8, "max": 64, "default": 8}


def test_general_settings_save_persist_and_are_audited(admin_client):
    result = admin_client.put("/api/admin/settings/general", json=GENERAL).get_json()
    assert set(result["changed"]) == set(GENERAL)
    assert result["settings"]["primary_color"] == "#117a65"  # normalised to lower case
    assert db.session.get(SystemSetting, "locale.timezone").value == "Asia/Makassar"

    log = db.session.scalar(select(ActivityLog).where(ActivityLog.entity_type == "system_settings"))
    assert log.before["app_name"] == "SchoolSync AI" and log.after["app_name"] == "Sekolah Pintar"

    html = admin_client.get("/admin/").data
    assert b"Sekolah Pintar" in html and b"--accent: #117a65" in html
    assert b'data-theme="dark"' in html and b'data-locale="en-GB"' in html and b'data-timezone="Asia/Makassar"' in html

    again = admin_client.put("/api/admin/settings/general", json={**GENERAL, "primary_color": "#117a65"}).get_json()
    assert again["changed"] == []


@pytest.mark.parametrize("field, value", [
    ("primary_color", "red; background:url(x)"), ("primary_color", "#12345"), ("timezone", "Mars/Olympus"),
    ("theme_mode", "neon"), ("default_language", "fr"), ("app_name", "A"),
])
def test_general_settings_are_validated(admin_client, field, value):
    response = admin_client.put("/api/admin/settings/general", json={**GENERAL, field: value})
    assert response.status_code == 422 and field in response.get_json()["fields"]


def test_unknown_fields_are_rejected(admin_client):
    response = admin_client.put("/api/admin/settings/general", json={**GENERAL, "security.lockout_minutes": 0})
    assert response.status_code == 422


@pytest.mark.parametrize("field, value", [
    ("session_timeout_minutes", 1), ("max_failed_logins", 2), ("lockout_minutes", 0), ("password_min_length", 6),
    ("password_min_length", 65), ("max_failed_logins", "five"),
])
def test_security_settings_are_bounded(admin_client, field, value):
    response = admin_client.put("/api/admin/settings/security", json={**SECURITY, field: value})
    assert response.status_code == 422 and field in response.get_json()["fields"]


def test_security_settings_apply_to_login_and_passwords(admin_client, client, make_user):
    assert admin_client.put("/api/admin/settings/security", json=SECURITY).status_code == 200
    assert ss.lockout_policy() == (4, 10)
    assert ss.password_min_length() == 12

    response = admin_client.post("/api/admin/users", json={
        "full_name": "New Teacher", "email": "t@example.com", "role_id": 1, "password": "elevenchars"})
    assert response.status_code == 422 and "12" in response.get_json()["fields"]["password"]


def test_admin_role_can_edit_general_but_not_security(client, make_user):
    login(client, make_user("admin").email)
    data = client.get("/api/admin/settings").get_json()
    assert data["can_edit"] and not data["can_edit_security"]
    assert client.put("/api/admin/settings/general", json=GENERAL).status_code == 200
    assert client.put("/api/admin/settings/security", json=SECURITY).status_code == 403
    assert db.session.get(SystemSetting, "security.max_failed_logins").value == 5


def test_viewer_can_read_nothing_and_content_manager_is_blocked(client, make_user):
    login(client, make_user("content_manager").email)
    assert client.get("/api/admin/settings").status_code == 403
    assert client.put("/api/admin/settings/general", json=GENERAL).status_code == 403


def test_damaged_setting_rows_fall_back_to_safe_values(app):
    db.session.get(SystemSetting, "security.max_failed_logins").value = "lots"
    db.session.get(SystemSetting, "security.lockout_minutes").value = 100000
    db.session.get(SystemSetting, "theme.mode").value = "<script>"
    db.session.commit()
    assert ss.lockout_policy() == (5, 1440)
    assert ss.branding()["theme"] == "light"


def test_logo_upload_and_remove(admin_client, app):
    response = admin_client.post("/api/admin/settings/logo", data={"logo": (io.BytesIO(PNG), "logo.png")},
                                 content_type="multipart/form-data")
    assert response.status_code == 201
    url = response.get_json()["logo_url"]
    assert url.startswith("/media/logos/") and ss.get("app.logo_url") == url
    assert admin_client.get(url).status_code == 200
    assert url.encode() in admin_client.get("/admin/").data

    assert admin_client.delete("/api/admin/settings/logo").status_code == 204
    assert ss.get("app.logo_url") is None
    assert not (app.config["UPLOAD_FOLDER"] / "logos" / url.rsplit("/", 1)[1]).exists()


def test_logo_rejects_non_images(admin_client):
    response = admin_client.post("/api/admin/settings/logo", data={"logo": (io.BytesIO(b"<svg onload=x>"), "logo.svg")},
                                 content_type="multipart/form-data")
    assert response.status_code == 422 and ss.get("app.logo_url") is None


def test_security_permission_implies_read_on_roles(admin_client):
    response = admin_client.post("/api/admin/roles", json={"name": "security_officer", "permissions": ["dashboard.view", "settings.security"]})
    assert set(response.get_json()["permissions"]) == {"dashboard.view", "settings.security", "settings.read"}


# --- Idle session timeout ----------------------------------------------------------------

def _set_last_seen(client, seconds_ago):
    with client.session_transaction() as session:
        session["_last_seen"] = int(time.time()) - seconds_ago


def test_idle_session_is_signed_out(admin_client):
    db.session.get(SystemSetting, "security.session_timeout_minutes").value = 10
    db.session.commit()
    _set_last_seen(admin_client, 9 * 60)
    assert admin_client.get("/admin/").status_code == 200  # still inside the window

    _set_last_seen(admin_client, 11 * 60)
    response = admin_client.get("/admin/events")
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
    assert admin_client.get("/api/admin/me").status_code == 401
    assert db.session.scalar(select(ActivityLog).where(ActivityLog.action == "session_expired")) is not None


def test_expired_api_request_gets_json_401(admin_client):
    _set_last_seen(admin_client, 9 * 60 * 60)  # default timeout is 8 hours
    response = admin_client.get("/api/admin/me")
    assert response.status_code == 401 and "signed out" in response.get_json()["error"]


def test_background_polls_do_not_count_as_activity(admin_client):
    _set_last_seen(admin_client, 120)
    admin_client.get("/api/me/notifications", headers={"X-Background-Request": "1"})
    with admin_client.session_transaction() as session:
        assert int(time.time()) - session["_last_seen"] >= 119

    admin_client.get("/api/me/notifications")
    with admin_client.session_transaction() as session:
        assert int(time.time()) - session["_last_seen"] < 5


# --- My profile ------------------------------------------------------------------------------

def test_profile_page_and_details(admin_client):
    assert admin_client.get("/admin/profile").status_code == 200
    data = admin_client.get("/api/me/profile").get_json()
    assert data["role"]["name"] == "super_admin" and data["password_min_length"] == 8


def test_name_change_needs_no_password(admin_client):
    me = admin_client.get("/api/me/profile").get_json()
    result = admin_client.put("/api/me/profile", json={"full_name": "Renamed Person", "email": me["email"]}).get_json()
    assert result["changed"] == ["full_name"] and result["full_name"] == "Renamed Person"
    assert db.session.scalar(select(ActivityLog).where(ActivityLog.summary.like("Updated own profile%"))) is not None


def test_email_change_needs_current_password_and_keeps_this_session(admin_client, app):
    me = admin_client.get("/api/me/profile").get_json()
    wrong = admin_client.put("/api/me/profile", json={"full_name": me["full_name"], "email": "new@example.com",
                                                     "current_password": "nope"})
    assert wrong.status_code == 422 and "current_password" in wrong.get_json()["fields"]

    user = db.session.get(User, me["id"])
    old_token = user.session_token
    ok = admin_client.put("/api/me/profile", json={"full_name": me["full_name"], "email": "New@Example.com",
                                                  "current_password": PASSWORD})
    assert ok.status_code == 200 and ok.get_json()["email"] == "new@example.com"
    db.session.refresh(user)
    assert user.session_token != old_token          # other devices signed out
    assert admin_client.get("/api/admin/me").status_code == 200  # but not this one


def test_email_must_be_unique(admin_client, make_user):
    other = make_user("viewer")
    me = admin_client.get("/api/me/profile").get_json()
    response = admin_client.put("/api/me/profile", json={"full_name": me["full_name"], "email": other.email,
                                                        "current_password": PASSWORD})
    assert response.status_code == 422 and "email" in response.get_json()["fields"]


def test_password_change(admin_client, app, make_user):
    me = admin_client.get("/api/me/profile").get_json()
    body = {"current_password": PASSWORD, "new_password": "a-brand-new-pass", "confirm_password": "a-brand-new-pass"}

    assert admin_client.put("/api/me/password", json={**body, "current_password": "wrong"}).status_code == 422
    assert db.session.scalar(select(ActivityLog).where(ActivityLog.action == "password_check_failed")) is not None
    mismatch = admin_client.put("/api/me/password", json={**body, "confirm_password": "different"})
    assert "confirm_password" in mismatch.get_json()["fields"]
    same = admin_client.put("/api/me/password", json={**body, "new_password": PASSWORD, "confirm_password": PASSWORD})
    assert "new_password" in same.get_json()["fields"]

    db.session.get(SystemSetting, "security.password_min_length").value = 20
    db.session.commit()
    short = admin_client.put("/api/me/password", json=body)
    assert short.status_code == 422 and "20" in short.get_json()["fields"]["new_password"]
    db.session.get(SystemSetting, "security.password_min_length").value = 8
    db.session.commit()

    assert admin_client.put("/api/me/password", json=body).status_code == 200
    assert admin_client.get("/api/admin/me").status_code == 200  # still signed in here
    user = db.session.get(User, me["id"])
    assert user.check_password("a-brand-new-pass") and not user.check_password(PASSWORD)


def test_regular_users_can_use_profile_api_but_not_admin_page(user_client):
    assert user_client.get("/api/me/profile").status_code == 200
    assert user_client.get("/admin/profile").status_code == 403


def test_profile_api_requires_login(client):
    assert client.get("/api/me/profile").status_code == 401


# --- Dashboard -----------------------------------------------------------------------------

def _chat(user, when, sources):
    session = ChatSession(user_id=user.id, started_at=when, last_message_at=when)
    db.session.add(session)
    db.session.flush()
    for source in sources:
        question = ChatMessage(session_id=session.id, role="user", content="q", created_at=when)
        db.session.add(question)
        db.session.flush()
        db.session.add(ChatMessage(session_id=session.id, role="assistant", content="a", in_reply_to_id=question.id,
                                   resolved_by=source, created_at=when, needs_review=source == "fallback"))
    db.session.commit()


def test_dashboard_numbers_come_from_the_database(admin_client, make_user):
    user = make_user("user")
    now = utcnow()
    _chat(user, now - timedelta(hours=1), ["configured", "knowledge", "fallback"])
    _chat(user, now - timedelta(days=3), ["ai"])
    _chat(user, now - timedelta(days=40), ["configured"])  # outside the 30-day window

    data = admin_client.get("/api/admin/dashboard?days=30").get_json()
    t = data["totals"]
    assert t["conversations"] == 2 and t["questions_asked"] == 4
    assert t["answer_rate"] == 0.75 and t["needs_review"] == 1
    assert t["upcoming_events"] == len([e for e in db.session.scalars(select(Event))
                                        if e.status == "upcoming" and e.start_at >= now])
    assert {s["source"]: s["count"] for s in data["sources"]} == {"configured": 1, "knowledge": 1, "ai": 1, "fallback": 1}
    assert len(data["daily"]) == 30 and sum(d["conversations"] for d in data["daily"]) == 2
    assert data["daily"][-1]["date"] == data["period"]["last"]

    ninety = admin_client.get("/api/admin/dashboard?days=90").get_json()
    assert ninety["totals"]["conversations"] == 3 and len(ninety["daily"]) == 90
    assert admin_client.get("/api/admin/dashboard?days=12345").get_json()["period"]["days"] == 30


def test_dashboard_previous_period_and_system_status(admin_client, make_user):
    user = make_user("user")
    _chat(user, utcnow() - timedelta(days=10), ["configured"])
    data = admin_client.get("/api/admin/dashboard?days=7").get_json()
    assert data["totals"]["conversations"] == 0 and data["totals"]["conversations_previous"] == 1
    assert data["totals"]["answer_rate"] is None
    states = {c["key"]: c["state"] for c in data["system"]}
    assert states["database"] == "ok" and states["ai"] in {"off", "ok", "warning"}
    assert set(states) == {"database", "ai", "notifications", "email"}


def test_dashboard_hides_activity_without_permission(client, make_user):
    login(client, make_user("content_manager").email)
    data = client.get("/api/admin/dashboard").get_json()
    assert data["activity"] is None and "totals" in data


def test_dashboard_page_is_a_shell(admin_client):
    assert b'id="usage-chart"' in admin_client.get("/admin/").data


def test_dashboard_api_needs_admin_access(user_client):
    assert user_client.get("/api/admin/dashboard").status_code == 403
