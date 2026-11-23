"""Step 9: user management and roles & permissions (with privilege-escalation guards)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import ActivityLog, Permission, Role, User
from app.models.base import utcnow
from app.seeds import seed_all

from .conftest import PASSWORD, login

USERS = "/api/admin/users"
ROLES = "/api/admin/roles"


def role_id(name):
    return db.session.scalar(select(Role.id).where(Role.name == name))


def as_role(app, make_user, role, **fields):
    user = make_user(role, **fields)
    client = app.test_client()
    login(client, user.email)
    return client, user


# --- Listing & profile ------------------------------------------------------------------

def test_list_filters_and_summary(admin_client, make_user):
    make_user("user", email="s1@example.com")
    make_user("user", email="gone@example.com", is_active=False)
    make_user("viewer", email="v@example.com", locked_until=utcnow() + timedelta(minutes=10))
    assert admin_client.get(f"{USERS}?status=inactive").get_json()["total"] == 1
    assert admin_client.get(f"{USERS}?status=locked").get_json()["total"] == 1
    assert admin_client.get(f"{USERS}?role={role_id('user')}").get_json()["total"] == 2
    assert admin_client.get(f"{USERS}?access=admin").get_json()["total"] == 2  # super admin + viewer
    assert admin_client.get(f"{USERS}?q=s1@").get_json()["items"][0]["email"] == "s1@example.com"
    summary = admin_client.get(f"{USERS}/summary").get_json()
    assert summary["total"] == 4 and summary["inactive"] == 1 and summary["locked"] == 1


def test_profile_detail_and_activity(admin_client, make_user, app):
    student = make_user("user", email="s1@example.com")
    login(app.test_client(), student.email)  # creates a "login" entry for them
    detail = admin_client.get(f"{USERS}/{student.id}").get_json()
    assert detail["permissions"] == [] and detail["role"]["admin_access"] is False
    assert detail["stats"]["actions"] == 1
    activity = admin_client.get(f"{USERS}/{student.id}/activity").get_json()
    assert activity["items"][0]["action"] == "login"


# --- Creating & editing ---------------------------------------------------------------------

def test_create_with_generated_password_shown_once(admin_client, app):
    created = admin_client.post(USERS, json={"full_name": "Siswa Baru", "email": "Baru@Example.com", "role_id": role_id("user")})
    data = created.get_json()
    assert created.status_code == 201 and data["email"] == "baru@example.com"
    temp = data["temporary_password"]
    assert len(temp) >= 12
    assert "temporary_password" not in admin_client.get(f"{USERS}/{data['id']}").get_json()
    assert temp not in db.session.get(User, data["id"]).password_hash  # stored hashed only
    fresh = app.test_client()
    assert login(fresh, "baru@example.com", temp).headers["Location"] == "/dashboard"


def test_create_with_chosen_password_and_inactive(admin_client):
    data = admin_client.post(USERS, json={"full_name": "Guru", "email": "guru@example.com", "role_id": role_id("viewer"),
                                          "password": "a-good-password", "is_active": False}).get_json()
    assert "temporary_password" not in data and data["is_active"] is False


@pytest.mark.parametrize("overrides,field", [
    ({"email": "not-an-email"}, "email"),
    ({"email": "admin@example.com"}, "email"),   # duplicate (see fixture)
    ({"full_name": "A"}, "full_name"),
    ({"role_id": None}, "role_id"),
    ({"role_id": 9999}, "role_id"),
    ({"password": "short"}, "password"),
])
def test_create_validation(admin_client, make_user, overrides, field):
    make_user("viewer", email="admin@example.com")
    body = {"full_name": "Valid Name", "email": "new@example.com", "role_id": role_id("user"), **overrides}
    response = admin_client.post(USERS, json=body)
    assert response.status_code == 422 and field in response.get_json()["fields"]


def test_password_minimum_follows_settings(admin_client):
    from app.models import SystemSetting
    db.session.get(SystemSetting, "security.password_min_length").value = 12
    db.session.commit()
    response = admin_client.post(USERS, json={"full_name": "X Y", "email": "x@example.com", "role_id": role_id("user"),
                                              "password": "elevenchars"})
    assert response.status_code == 422 and "12" in response.get_json()["fields"]["password"]


def test_edit_user_and_role_change_is_immediate(app, admin_client, make_user):
    target_client, target = as_role(app, make_user, "viewer")
    assert target_client.post("/api/admin/events", json={"title": "Nope", "start": "2030-01-01T08:00"}).status_code == 403
    admin_client.put(f"{USERS}/{target.id}", json={"full_name": "Pak Guru", "email": target.email, "role_id": role_id("content_manager")})
    assert target_client.post("/api/admin/events", json={"title": "Now allowed", "start": "2030-01-01T08:00"}).status_code == 201
    log = db.session.scalar(select(ActivityLog).where(ActivityLog.entity_type == "users", ActivityLog.action == "update"))
    assert log.before["role"] == "viewer" and log.after["role"] == "content_manager"


def test_changing_someones_email_signs_them_out(app, admin_client, make_user):
    target_client, target = as_role(app, make_user, "viewer")
    admin_client.put(f"{USERS}/{target.id}", json={"full_name": target.full_name, "email": "new-address@example.com", "role_id": role_id("viewer")})
    assert target_client.get("/admin/").status_code == 302


# --- Status, password, unlock -------------------------------------------------------------------

def test_deactivate_signs_out_and_blocks_login(app, admin_client, make_user):
    target_client, target = as_role(app, make_user, "viewer")
    assert admin_client.patch(f"{USERS}/{target.id}/status", json={"is_active": False}).get_json()["is_active"] is False
    assert target_client.get("/admin/").status_code == 302
    fresh = app.test_client()
    assert b"deactivated" in login(fresh, target.email).data
    assert admin_client.patch(f"{USERS}/{target.id}/status", json={"is_active": True}).get_json()["is_active"] is True
    assert login(fresh, target.email).status_code == 302


def test_reset_password(app, admin_client, make_user):
    target_client, target = as_role(app, make_user, "viewer", locked_until=None)
    result = admin_client.post(f"{USERS}/{target.id}/reset-password", json={}).get_json()
    assert target_client.get("/admin/").status_code == 302  # signed out everywhere
    fresh = app.test_client()
    assert login(fresh, target.email, PASSWORD).status_code == 200  # old password no longer works
    assert login(fresh, target.email, result["temporary_password"]).status_code == 302
    chosen = admin_client.post(f"{USERS}/{target.id}/reset-password", json={"password": "short"})
    assert chosen.status_code == 422


def test_unlock(admin_client, make_user):
    target = make_user("viewer", locked_until=utcnow() + timedelta(minutes=10), failed_login_attempts=3)
    data = admin_client.post(f"{USERS}/{target.id}/unlock").get_json()
    assert data["is_locked"] is False and data["failed_login_attempts"] == 0


# --- Privilege-escalation guards -------------------------------------------------------------------

def test_admin_cannot_create_or_promote_to_super_admin(app, make_user):
    client, _ = as_role(app, make_user, "admin")
    response = client.post(USERS, json={"full_name": "Boss", "email": "boss@example.com", "role_id": role_id("super_admin")})
    assert response.status_code == 422 and "more access" in response.get_json()["fields"]["role_id"]
    student = make_user("user")
    response = client.put(f"{USERS}/{student.id}", json={"full_name": "S", "email": student.email, "role_id": role_id("super_admin")})
    assert response.status_code == 422


def test_admin_cannot_touch_super_admin_accounts(app, make_user):
    client, _ = as_role(app, make_user, "admin")
    boss = make_user("super_admin")
    for method, path, body in [
        ("put", f"{USERS}/{boss.id}", {"full_name": "X", "email": boss.email, "role_id": role_id("super_admin")}),
        ("patch", f"{USERS}/{boss.id}/status", {"is_active": False}),
        ("post", f"{USERS}/{boss.id}/reset-password", {}),
        ("post", f"{USERS}/{boss.id}/unlock", None),
    ]:
        response = getattr(client, method)(path, json=body)
        assert response.status_code == 403, path
    assert client.get(f"{USERS}/{boss.id}").get_json()["can_manage"] is False


def test_content_manager_cannot_manage_users(app, make_user):
    client, _ = as_role(app, make_user, "content_manager")
    assert client.get(USERS).status_code == 403
    assert client.get("/admin/users").status_code == 403


def test_viewer_cannot_change_users(app, make_user):
    viewer_role = db.session.scalar(select(Role).where(Role.name == "viewer"))
    viewer_role.permissions.append(db.session.scalar(select(Permission).where(Permission.code == "users.read")))
    db.session.commit()
    client, _ = as_role(app, make_user, "viewer")
    assert client.get(USERS).status_code == 200
    student = make_user("user")
    assert client.patch(f"{USERS}/{student.id}/status", json={"is_active": False}).status_code == 403


def test_cannot_change_own_role_or_deactivate_self(admin_client):
    me = db.session.scalar(select(User).where(User.email.like("super_admin%")))
    response = admin_client.put(f"{USERS}/{me.id}", json={"full_name": "Me", "email": me.email, "role_id": role_id("viewer")})
    assert response.status_code == 422 and "own role" in response.get_json()["fields"]["role_id"]
    assert admin_client.patch(f"{USERS}/{me.id}/status", json={"is_active": False}).status_code == 409
    # Editing your own name is fine.
    assert admin_client.put(f"{USERS}/{me.id}", json={"full_name": "New Name", "email": me.email, "role_id": me.role_id}).status_code == 200


def test_last_super_admin_is_protected(app, make_user):
    """Someone with a custom all-permissions role counts as equal to a super admin, so the only
    thing stopping them from removing the last super admin is this guard."""
    all_permissions = list(db.session.scalars(select(Permission)))
    db.session.add(Role(name="owner", permissions=all_permissions))
    db.session.commit()
    boss = make_user("super_admin")
    owner_client, _ = as_role(app, make_user, "owner")

    response = owner_client.patch(f"{USERS}/{boss.id}/status", json={"is_active": False})
    assert response.status_code == 409 and "only active super admin" in response.get_json()["error"]
    response = owner_client.put(f"{USERS}/{boss.id}", json={"full_name": "Boss", "email": boss.email, "role_id": role_id("owner")})
    assert response.status_code == 422 and "only active super admin" in response.get_json()["fields"]["role_id"]

    # With a second super admin, the first may be demoted.
    make_user("super_admin")
    response = owner_client.put(f"{USERS}/{boss.id}", json={"full_name": "Boss", "email": boss.email, "role_id": role_id("admin")})
    assert response.status_code == 200


def test_two_super_admins_can_manage_each_other(app, make_user):
    client, _ = as_role(app, make_user, "super_admin")
    other = make_user("super_admin")
    assert client.patch(f"{USERS}/{other.id}/status", json={"is_active": False}).status_code == 200


# --- Roles -----------------------------------------------------------------------------------------

def test_roles_catalog(admin_client):
    data = admin_client.get(ROLES).get_json()
    assert [g["module"] for g in data["catalog"]][:2] == ["dashboard", "events"]
    super_admin = next(r for r in data["roles"] if r["name"] == "super_admin")
    assert super_admin["locked"] and len(super_admin["permissions"]) == 20


def test_create_role_edit_implies_view_and_assign(admin_client, app):
    created = admin_client.post(ROLES, json={"name": "Wali Kelas", "description": "Homeroom teacher",
                                             "permissions": ["dashboard.view", "events.write"]}).get_json()
    assert created["name"] == "wali_kelas"
    assert created["permissions"] == ["dashboard.view", "events.read", "events.write"]  # read added automatically
    user = admin_client.post(USERS, json={"full_name": "Bu Ani", "email": "ani@example.com", "role_id": created["id"],
                                          "password": "a-good-password"}).get_json()
    teacher = app.test_client()
    assert login(teacher, "ani@example.com", "a-good-password").headers["Location"] == "/admin/"
    assert teacher.get("/admin/events").status_code == 200
    assert teacher.get("/admin/knowledge").status_code == 403
    # deleting a role that still has users is refused
    assert admin_client.delete(f"{ROLES}/{created['id']}").status_code == 409
    admin_client.put(f"{USERS}/{user['id']}", json={"full_name": "Bu Ani", "email": "ani@example.com", "role_id": role_id("viewer")})
    assert admin_client.delete(f"{ROLES}/{created['id']}").status_code == 204


@pytest.mark.parametrize("body,field", [
    ({"name": "1bad"}, "name"),
    ({"name": "admin"}, "name"),                                 # exists
    ({"name": "ok_name", "permissions": ["events.fly"]}, "permissions"),
])
def test_role_validation(admin_client, body, field):
    response = admin_client.post(ROLES, json=body)
    assert response.status_code == 422 and field in response.get_json()["fields"]


def test_builtin_role_rules(admin_client):
    assert admin_client.put(f"{ROLES}/{role_id('admin')}", json={"name": "boss", "permissions": []}).status_code == 422
    assert admin_client.delete(f"{ROLES}/{role_id('viewer')}").status_code == 409
    all_codes = [p.code for p in db.session.scalars(select(Permission))]
    assert admin_client.put(f"{ROLES}/{role_id('super_admin')}", json={"name": "super_admin", "permissions": ["dashboard.view"]}).status_code == 409
    assert admin_client.put(f"{ROLES}/{role_id('super_admin')}", json={"name": "super_admin", "permissions": all_codes}).status_code == 200


def test_editing_builtin_role_permissions_applies_and_survives_seed(admin_client, app, make_user):
    admin_client.put(f"{ROLES}/{role_id('viewer')}", json={"name": "viewer", "permissions": ["dashboard.view", "events.read"]})
    viewer_client, _ = as_role(app, make_user, "viewer")
    assert viewer_client.get("/admin/knowledge").status_code == 403
    seed_all()  # re-running seed must not undo the admin's change
    codes = {p.code for p in db.session.get(Role, role_id("viewer")).permissions}
    assert codes == {"dashboard.view", "events.read"}


def test_roles_require_roles_manage(app, make_user):
    client, _ = as_role(app, make_user, "admin")
    assert client.get(ROLES).status_code == 403
    assert client.get("/admin/roles").status_code == 403


# --- Pages ------------------------------------------------------------------------------------------

def test_pages(admin_client, make_user):
    student = make_user("user")
    assert admin_client.get("/admin/users").status_code == 200
    page = admin_client.get(f"/admin/users/{student.id}")
    assert page.status_code == 200 and student.full_name.encode() in page.data
    assert admin_client.get("/admin/users/99999").status_code == 404
    assert admin_client.get("/admin/roles").status_code == 200
