"""Step 3: admin layout, navigation by permission, shared API helpers, activity log."""

import pytest
from pydantic import BaseModel, Field

from app.admin.navigation import ALL_ITEMS, NAVIGATION
from app.api.admin.utils import parse_body
from app.extensions import db
from app.models import ActivityLog, SystemSetting

from .conftest import login


def visible_items(client):
    html = client.get("/admin/").data
    return {i.endpoint for _, i in ALL_ITEMS.values()
            if f'href="/admin{i.path}"'.encode() in html or (i.path == "/" and b'href="/admin/"' in html)}


# --- Layout & navigation ------------------------------------------------------

def test_every_nav_item_has_a_route(app):
    rules = {rule.endpoint for rule in app.url_map.iter_rules()}
    for _, item in ALL_ITEMS.values():
        assert f"admin.{item.endpoint}" in rules


def test_super_admin_sees_every_module(admin_client):
    assert visible_items(admin_client) == set(ALL_ITEMS)


def test_content_manager_menu_is_filtered(client, make_user):
    login(client, make_user("content_manager").email)
    visible = visible_items(client)
    assert {"events", "questions", "knowledge", "monitoring"} <= visible
    assert not {"users", "roles", "settings", "activity"} & visible


def test_viewer_has_no_admin_management_items(client, make_user):
    login(client, make_user("viewer").email)
    visible = visible_items(client)
    assert "events" in visible
    assert not {"users", "roles", "settings", "activity"} & visible


@pytest.mark.parametrize("endpoint", sorted(ALL_ITEMS))
def test_every_module_page_renders_for_super_admin(admin_client, endpoint):
    _, item = ALL_ITEMS[endpoint]
    response = admin_client.get("/admin" + item.path)
    assert response.status_code == 200
    assert b'aria-current="page"' in response.data  # active menu item highlighted


@pytest.mark.parametrize("path", ["/admin/users", "/admin/roles", "/admin/settings", "/admin/activity"])
def test_content_manager_blocked_from_admin_only_pages(client, make_user, path):
    login(client, make_user("content_manager").email)
    assert client.get(path).status_code == 403


def test_coming_soon_page_names_step_and_permission(admin_client):
    html = admin_client.get("/admin/settings").data
    assert b"step 10" in html and b"settings.read" in html


def test_breadcrumb_shows_section_and_page(admin_client):
    html = admin_client.get("/admin/chat/questions").data
    assert b"AI Assistant" in html and b"Questions &amp; answers" in html


def test_branding_comes_from_system_settings(app, admin_client):
    db.session.get(SystemSetting, "app.name").value = "Sekolah Pintar"
    db.session.get(SystemSetting, "theme.primary_color").value = "#117a65"
    db.session.commit()
    html = admin_client.get("/admin/").data
    assert b"Sekolah Pintar" in html and b"--accent: #117a65" in html


def test_unsafe_brand_color_is_ignored(app, admin_client):
    db.session.get(SystemSetting, "theme.primary_color").value = "red; background:url(x)"
    db.session.commit()
    html = admin_client.get("/admin/").data
    assert b"--accent: #4f63df" in html and b"url(x)" not in html


def test_overview_shows_real_counts(admin_client):
    html = admin_client.get("/admin/").data.decode()
    assert "Scheduled events" in html
    assert '<p class="stat-value">5</p>' in html  # five seeded events


# --- Activity log API -----------------------------------------------------------

def add_logs(n, **fields):
    for i in range(n):
        db.session.add(ActivityLog(action=fields.get("action", "update"),
                                   summary=fields.get("summary", f"Entry {i}"),
                                   entity_type=fields.get("entity_type", "events")))
    db.session.commit()


def test_activity_list_contract_and_pagination(admin_client):
    add_logs(30)
    data = admin_client.get("/api/admin/activity?per_page=10&page=2").get_json()
    assert set(data) == {"items", "page", "per_page", "total", "pages"}
    assert data["page"] == 2 and data["per_page"] == 10 and len(data["items"]) == 10
    assert data["total"] >= 30
    assert data["items"][0]["created_at"].endswith("Z")


def test_activity_page_out_of_range_clamps(admin_client):
    data = admin_client.get("/api/admin/activity?page=999").get_json()
    assert data["page"] == data["pages"]


def test_activity_bad_paging_params_fall_back(admin_client):
    data = admin_client.get("/api/admin/activity?page=abc&per_page=100000").get_json()
    assert data["page"] == 1 and data["per_page"] == 100


def test_activity_filter_and_search(admin_client):
    add_logs(3, action="delete", summary="Deleted question about exams")
    add_logs(2, action="update", summary="Changed something else")
    data = admin_client.get("/api/admin/activity?action=delete").get_json()
    assert data["total"] == 3 and {i["action"] for i in data["items"]} == {"delete"}
    data = admin_client.get("/api/admin/activity?q=exams").get_json()
    assert data["total"] == 3


def test_activity_search_treats_wildcards_literally(admin_client):
    add_logs(1, summary="Discount 100% applied")
    add_logs(1, summary="Nothing special")
    assert admin_client.get("/api/admin/activity?q=%25").get_json()["total"] == 1


def test_activity_search_matches_user(admin_client):
    data = admin_client.get("/api/admin/activity?q=super_admin").get_json()
    assert data["total"] >= 1 and data["items"][0]["actor"]["email"].startswith("super_admin")


def test_activity_facets(admin_client):
    add_logs(1, action="delete", entity_type="knowledge_entries")
    data = admin_client.get("/api/admin/activity/facets").get_json()
    assert "delete" in data["actions"] and "knowledge_entries" in data["entity_types"]


def test_activity_api_requires_permission(client, make_user):
    login(client, make_user("content_manager").email)
    assert client.get("/api/admin/activity").status_code == 403


# --- Validation helper -----------------------------------------------------------

class SampleBody(BaseModel):
    title: str = Field(min_length=3)
    count: int


def test_parse_body_returns_field_errors(app):
    with app.test_request_context(json={"title": "x", "count": "many"}):
        with pytest.raises(Exception) as caught:
            parse_body(SampleBody)
    error = caught.value
    assert error.status == 422
    assert set(error.fields) == {"title", "count"}


def test_parse_body_rejects_non_object(app):
    with app.test_request_context(json=[1, 2]):
        with pytest.raises(Exception) as caught:
            parse_body(SampleBody)
    assert caught.value.status == 400


def test_parse_body_success(app):
    with app.test_request_context(json={"title": "Hello", "count": 2}):
        assert parse_body(SampleBody).count == 2
