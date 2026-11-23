"""Step 6: events & schedules (timezones, validation, reminders, list/calendar, status)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import ActivityLog, Event, EventReminder, SystemSetting
from app.models.base import utcnow
from app.services.timezone import local_tz, to_local

from .conftest import login

API = "/api/admin/events"


def make(client, **overrides):
    body = {"title": "Rapat Guru", "category": "meeting", "status": "upcoming", "all_day": False,
            "start": "2030-03-10T09:00", "end": "2030-03-10T10:30", "location": "Ruang guru",
            "description": None, "reminders": []}
    body.update(overrides)
    response = client.post(API, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def local_str(dt_utc, fmt="%Y-%m-%dT%H:%M"):
    return to_local(dt_utc, local_tz()).strftime(fmt)


# --- Create & timezone handling ---------------------------------------------------------

def test_create_converts_local_time_to_utc(admin_client):
    data = make(admin_client, reminders=[{"offset_minutes": 60}, {"offset_minutes": 1440, "channel": "email"}])
    assert data["start_at"] == "2030-03-10T02:00:00Z"  # Asia/Jakarta is UTC+7
    assert data["start"] == "2030-03-10T09:00" and data["end_time"] == "10:30"
    assert {(r["offset_minutes"], r["channel"]) for r in data["reminders"]} == {(60, "in_app"), (1440, "email")}
    assert next(r for r in data["reminders"] if r["offset_minutes"] == 60)["fires_at"] == "2030-03-10T08:00"
    log = db.session.scalar(select(ActivityLog).where(ActivityLog.entity_type == "events", ActivityLog.action == "create"))
    assert "Rapat Guru" in log.summary


def test_timezone_follows_system_settings(admin_client):
    db.session.get(SystemSetting, "locale.timezone").value = "UTC"
    db.session.commit()
    data = make(admin_client)
    assert data["start_at"] == "2030-03-10T09:00:00Z" and data["start"] == "2030-03-10T09:00"


def test_all_day_multi_day_event(admin_client):
    data = make(admin_client, title="Libur Semester", all_day=True, start="2030-12-24", end="2030-12-26")
    assert data["start"] == "2030-12-24" and data["end"] == "2030-12-26"
    assert data["start_date"] == "2030-12-24" and data["end_date"] == "2030-12-26"
    assert data["start_time"] is None


def test_category_is_normalised(admin_client):
    assert make(admin_client, category="  Field   Trip ")["category"] == "field trip"
    assert make(admin_client, category="")["category"] == "general"


# --- Validation -----------------------------------------------------------------------------------

@pytest.mark.parametrize("overrides,field", [
    ({"end": "2030-03-10T08:00"}, "end"),
    ({"start": "not a date"}, "start"),
    ({"start": "1999-01-01T08:00", "end": None}, "start"),
    ({"start": "2030-03-10T09:00+07:00", "end": None}, "start"),
    ({"all_day": True, "start": "2030-03-10", "end": "2030-03-09"}, "end"),
    ({"reminders": [{"offset_minutes": 60}, {"offset_minutes": 60}]}, "reminders"),
    ({"reminders": [{"offset_minutes": 60, "channel": "sms"}]}, "reminders.0.channel"),
    ({"reminders": [{"offset_minutes": -5}]}, "reminders.0.offset_minutes"),
    ({"reminders": [{"offset_minutes": m} for m in range(6)]}, "reminders"),
    ({"status": "postponed"}, "status"),
    ({"category": "exam!!"}, "category"),
    ({"title": " "}, "title"),
])
def test_validation(admin_client, overrides, field):
    body = {"title": "Valid", "start": "2030-03-10T09:00", "end": "2030-03-10T10:00", **overrides}
    response = admin_client.post(API, json=body)
    assert response.status_code == 422, response.get_json()
    assert field in response.get_json()["fields"]


# --- Update & reminders ---------------------------------------------------------------------------

def test_moving_event_resets_sent_reminders(admin_client):
    data = make(admin_client, reminders=[{"offset_minutes": 60}])
    reminder = db.session.scalar(select(EventReminder).where(EventReminder.event_id == data["id"]))
    reminder.is_sent = True
    db.session.commit()

    same_time = {**data, "reminders": [{"offset_minutes": 60, "channel": "in_app"}]}
    body = {k: same_time[k] for k in ("title", "category", "status", "all_day", "start", "end", "location", "description", "reminders")}
    admin_client.put(f"{API}/{data['id']}", json={**body, "title": "Renamed"})
    db.session.refresh(reminder)
    assert reminder.is_sent is True  # unchanged time: keep "already sent"

    admin_client.put(f"{API}/{data['id']}", json={**body, "start": "2030-03-11T09:00", "end": "2030-03-11T10:00"})
    db.session.refresh(reminder)
    assert reminder.is_sent is False  # moved: it must fire again


def test_update_replaces_reminders(admin_client):
    data = make(admin_client, reminders=[{"offset_minutes": 60}, {"offset_minutes": 1440}])
    body = {"title": "Rapat Guru", "start": "2030-03-10T09:00", "reminders": [{"offset_minutes": 15}]}
    updated = admin_client.put(f"{API}/{data['id']}", json=body).get_json()
    assert [r["offset_minutes"] for r in updated["reminders"]] == [15]
    assert db.session.scalar(select(func.count(EventReminder.id))) == 1


def test_set_status_and_delete(admin_client):
    data = make(admin_client, reminders=[{"offset_minutes": 60}])
    assert admin_client.patch(f"{API}/{data['id']}/status", json={"status": "cancelled"}).get_json()["status"] == "cancelled"
    assert admin_client.patch(f"{API}/{data['id']}/status", json={"status": "nope"}).status_code == 422
    assert admin_client.delete(f"{API}/{data['id']}").status_code == 204
    assert db.session.get(Event, data["id"]) is None
    assert db.session.scalar(select(func.count(EventReminder.id))) == 0
    assert admin_client.get(f"{API}/{data['id']}").status_code == 404


# --- Status checks -----------------------------------------------------------------------------------

def test_past_event_still_upcoming_is_flagged(admin_client):
    yesterday = (to_local(utcnow(), local_tz()) - timedelta(days=1)).strftime("%Y-%m-%dT09:00")
    data = make(admin_client, title="Forgot to close", start=yesterday, end=None)
    assert data["needs_status_update"] is True and data["is_past"] is True
    assert admin_client.get(f"{API}/summary").get_json()["needs_update"] == 1
    flagged = admin_client.get(f"{API}?needs_update=yes").get_json()
    assert [e["title"] for e in flagged["items"]] == ["Forgot to close"]
    admin_client.patch(f"{API}/{data['id']}/status", json={"status": "completed"})
    assert admin_client.get(f"{API}/summary").get_json()["needs_update"] == 0


def test_summary_counts(admin_client):
    make(admin_client)
    summary = admin_client.get(f"{API}/summary").get_json()
    assert summary["upcoming"] == 1 and summary["completed"] == 5 and summary["total"] == 6


# --- Listing & calendar -------------------------------------------------------------------------------

def test_list_filters(admin_client):
    make(admin_client, title="Future A", start="2030-01-05T08:00", end=None)
    make(admin_client, title="Future B", start="2030-01-02T08:00", end=None, category="exam")
    upcoming = admin_client.get(f"{API}?when=upcoming").get_json()
    assert [e["title"] for e in upcoming["items"]] == ["Future B", "Future A"]  # soonest first
    past = admin_client.get(f"{API}?when=past").get_json()
    assert past["total"] == 5 and past["items"][0]["start_date"] >= past["items"][-1]["start_date"]  # newest first
    assert admin_client.get(f"{API}?category=exam").get_json()["total"] == 3  # 2 seeded exams + Future B
    assert admin_client.get(f"{API}?q=future b").get_json()["total"] == 1
    ranged = admin_client.get(f"{API}?from=2030-01-01&to=2030-01-03").get_json()
    assert [e["title"] for e in ranged["items"]] == ["Future B"]


def test_today_filter_uses_local_day(admin_client):
    today = to_local(utcnow(), local_tz()).strftime("%Y-%m-%d")
    make(admin_client, title="Today late", start=f"{today}T23:30", end=None)
    assert "Today late" in [e["title"] for e in admin_client.get(f"{API}?when=today").get_json()["items"]]


def test_calendar_range_and_overlap(admin_client):
    make(admin_client, title="Spans months", all_day=True, start="2030-01-30", end="2030-02-02")
    make(admin_client, title="February only", start="2030-02-15T10:00", end=None)
    feb = admin_client.get(f"{API}/calendar?start=2030-02-01&end=2030-02-28").get_json()
    assert {e["title"] for e in feb["items"]} == {"Spans months", "February only"}
    jan = admin_client.get(f"{API}/calendar?start=2030-01-01&end=2030-01-29").get_json()
    assert jan["items"] == []
    assert "today" in feb


@pytest.mark.parametrize("query", ["", "?start=2030-01-01", "?start=2030-02-01&end=2030-01-01",
                                   "?start=2030-01-01&end=2030-06-01", "?start=bad&end=2030-01-02"])
def test_calendar_rejects_bad_ranges(admin_client, query):
    assert admin_client.get(f"{API}/calendar{query}").status_code == 400


def test_categories_include_defaults_and_used(admin_client):
    make(admin_client, category="sports day")
    categories = admin_client.get(f"{API}/categories").get_json()
    assert "exam" in categories and "sports day" in categories


# --- Permissions, dashboard, page --------------------------------------------------------------------

def test_permissions(client, make_user):
    login(client, make_user("viewer").email)
    assert client.get(API).status_code == 200
    assert client.post(API, json={"title": "Nope", "start": "2030-01-01T08:00"}).status_code == 403
    assert b'id="new-event"' not in client.get("/admin/events").data


def test_regular_user_has_no_access(client, make_user):
    login(client, make_user("user").email)
    assert client.get(API).status_code == 403


def test_content_manager_can_manage_events(client, make_user):
    login(client, make_user("content_manager").email)
    assert make(client)["created_by"] == "Content_Manager"


def test_dashboard_shows_new_upcoming_event(admin_client):
    make(admin_client, title="Pentas Seni", start="2030-05-01T08:00", end=None)
    data = admin_client.get("/api/admin/dashboard").get_json()
    assert "Pentas Seni" in [e["title"] for e in data["upcoming_events"]]  # no end time still counts
    assert data["totals"]["upcoming_events"] >= 1


def test_events_page_renders(admin_client):
    page = admin_client.get("/admin/events")
    assert page.status_code == 200 and b"Events &amp; schedules" in page.data and b"Asia/Jakarta" in page.data
