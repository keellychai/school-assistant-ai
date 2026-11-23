"""Step 2: authentication, lockout, session handling, RBAC and route protection."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.auth.security import is_safe_next, permission_required
from app.extensions import db
from app.models import ActivityLog, SystemSetting, User
from app.models.base import utcnow

from .conftest import PASSWORD, login


def actions():
    return [a.action for a in db.session.scalars(select(ActivityLog).order_by(ActivityLog.id))]


# --- Login / logout ----------------------------------------------------------

def test_login_page_renders(app, client):
    app.config["WTF_CSRF_ENABLED"] = True  # testing config disables CSRF; the field only renders when on
    response = client.get("/auth/login")
    assert response.status_code == 200
    assert b'name="csrf_token"' in response.data
    assert b"studentName" not in response.data


def test_root_redirects_anonymous_to_login(client):
    assert client.get("/").headers["Location"].startswith("/auth/login")


def test_admin_login_lands_on_admin_panel(client, make_user):
    admin = make_user("super_admin")
    response = login(client, admin.email)
    assert response.status_code == 302 and response.headers["Location"] == "/admin/"
    assert client.get("/admin/").status_code == 200
    db.session.refresh(admin)
    assert admin.last_login_at is not None
    assert "login" in actions()


def test_email_is_case_insensitive(client, make_user):
    admin = make_user("admin", email="mixed@example.com")
    assert login(client, "  MIXED@Example.COM ").headers["Location"] == "/admin/"


def test_regular_user_lands_on_dashboard_and_cannot_open_admin(client, make_user):
    user = make_user("user")
    assert login(client, user.email).headers["Location"] == "/dashboard"
    assert client.get("/admin/").status_code == 403
    assert client.get("/api/admin/me").status_code == 403
    assert actions().count("permission_denied") == 2


def test_wrong_password_shows_generic_error(client, make_user):
    user = make_user("admin")
    response = login(client, user.email, "wrong-password")
    assert response.status_code == 200
    assert b"Incorrect email or password." in response.data
    db.session.refresh(user)
    assert user.failed_login_attempts == 1
    assert "login_failed" in actions()


def test_unknown_email_shows_same_error(client):
    response = login(client, "nobody@example.com", "whatever-pass")
    assert b"Incorrect email or password." in response.data


def test_password_is_never_logged(client, make_user):
    user = make_user("admin")
    login(client, user.email, "super-secret-typo")
    for log in db.session.scalars(select(ActivityLog)):
        assert "super-secret-typo" not in (log.summary or "")


def test_logout_requires_post_and_ends_session(admin_client):
    assert admin_client.get("/auth/logout").status_code == 405
    assert admin_client.post("/auth/logout").status_code == 302
    assert admin_client.get("/admin/").headers["Location"].startswith("/auth/login")
    assert "logout" in actions()


def test_logged_in_user_visiting_login_is_redirected(admin_client):
    assert admin_client.get("/auth/login").headers["Location"] == "/admin/"


# --- Lockout ---------------------------------------------------------------

def test_account_locks_after_max_failed_attempts(client, make_user):
    user = make_user("admin")
    for _ in range(5):
        login(client, user.email, "wrong-password")
    db.session.refresh(user)
    assert user.is_locked
    assert "account_locked" in actions()

    # Even the correct password is refused while locked.
    response = login(client, user.email)
    assert response.status_code == 200 and b"Too many failed attempts" in response.data

    # After the lock expires, the correct password works again.
    user.locked_until = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert login(client, user.email).status_code == 302


def test_lockout_threshold_comes_from_system_settings(client, make_user):
    db.session.get(SystemSetting, "security.max_failed_logins").value = 2
    db.session.commit()
    user = make_user("admin")
    login(client, user.email, "wrong-password")
    login(client, user.email, "wrong-password")
    db.session.refresh(user)
    assert user.is_locked


def test_successful_login_resets_failed_counter(client, make_user):
    user = make_user("admin")
    login(client, user.email, "wrong-password")
    login(client, user.email)
    db.session.refresh(user)
    assert user.failed_login_attempts == 0


# --- Deactivation & session invalidation ------------------------------------

def test_deactivated_user_cannot_log_in(client, make_user):
    user = make_user("admin", is_active=False)
    response = login(client, user.email)
    assert response.status_code == 200 and b"deactivated" in response.data


def test_deactivating_user_ends_existing_session(client, make_user):
    user = make_user("admin")
    login(client, user.email)
    assert client.get("/admin/").status_code == 200
    user.is_active = False
    db.session.commit()
    assert client.get("/admin/").headers["Location"].startswith("/auth/login")


def test_password_change_signs_out_other_sessions(client, make_user):
    user = make_user("admin")
    login(client, user.email)
    user.set_password("a-brand-new-password")
    db.session.commit()
    assert client.get("/admin/").status_code == 302


def test_cookie_holds_token_not_numeric_id(app, make_user):
    user = make_user("admin")
    assert user.get_id() == user.session_token and len(user.get_id()) == 64


# --- Route protection --------------------------------------------------------

@pytest.mark.parametrize("path", ["/admin/", "/dashboard", "/chatbot"])
def test_pages_redirect_anonymous_to_login_with_next(client, path):
    location = client.get(path).headers["Location"]
    assert location.startswith("/auth/login?next=")


def test_admin_api_returns_json_401_when_anonymous(client):
    response = client.get("/api/admin/me")
    assert response.status_code == 401 and response.get_json() == {"error": "Authentication required."}


def test_ask_requires_login(client):
    assert client.post("/api/ask", json={"question": "hi"}).status_code == 401


def test_me_endpoint_lists_permissions(admin_client):
    data = admin_client.get("/api/admin/me").get_json()
    assert data["role"] == "super_admin" and "roles.manage" in data["permissions"]


def test_next_redirect_after_login(client, make_user):
    user = make_user("admin")
    response = client.post("/auth/login?next=/chatbot", data={"email": user.email, "password": PASSWORD})
    assert response.headers["Location"] == "/chatbot"


@pytest.mark.parametrize("target", ["https://evil.com", "//evil.com", "/\\evil.com", "javascript:alert(1)", "evil"])
def test_open_redirects_are_blocked(target):
    assert not is_safe_next(target)


def test_open_redirect_falls_back_home(client, make_user):
    user = make_user("admin")
    response = client.post("/auth/login?next=https://evil.com",
                           data={"email": user.email, "password": PASSWORD})
    assert response.headers["Location"] == "/admin/"


# --- permission_required decorator ------------------------------------------

@pytest.fixture()
def guarded_routes(app):
    @permission_required("users.write")
    def users_write():
        return {"ok": True}

    @permission_required("users.write", "chatconfig.write", any_of=True)
    def either():
        return {"ok": True}

    app.add_url_rule("/api/admin/_test/users-write", view_func=users_write)
    app.add_url_rule("/api/admin/_test/either", view_func=either)


def test_permission_required_denies_missing_permission(guarded_routes, client, make_user):
    login(client, make_user("content_manager").email)
    response = client.get("/api/admin/_test/users-write")
    assert response.status_code == 403
    assert response.get_json() == {"error": "You do not have permission to do that."}
    denied = db.session.scalar(select(ActivityLog).where(ActivityLog.action == "permission_denied"))
    assert "users.write" in denied.summary


def test_permission_required_any_of(guarded_routes, client, make_user):
    login(client, make_user("content_manager").email)
    assert client.get("/api/admin/_test/either").status_code == 200


def test_permission_required_allows_super_admin(guarded_routes, admin_client):
    assert admin_client.get("/api/admin/_test/users-write").status_code == 200


def test_role_change_takes_effect_immediately(guarded_routes, client, make_user):
    from app.models import Role
    user = make_user("content_manager")
    login(client, user.email)
    assert client.get("/api/admin/_test/users-write").status_code == 403
    user.role = db.session.scalar(select(Role).where(Role.name == "admin"))
    db.session.commit()
    assert client.get("/api/admin/_test/users-write").status_code == 200


# --- CSRF ---------------------------------------------------------------------

def test_login_without_csrf_token_is_rejected(app, client, make_user):
    app.config["WTF_CSRF_ENABLED"] = True
    user = make_user("admin")
    response = login(client, user.email)
    assert response.status_code == 400
    assert db.session.get(User, user.id).last_login_at is None
