"""Step 7: notifications (send/schedule/draft, delivery, inbox, event reminders, email, worker)."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import EventReminder, Notification, Role, User, UserNotification
from app.models.base import utcnow
from app.services import notifications as service
from app.services.timezone import local_tz, to_local

from .conftest import PASSWORD, login

API = "/api/admin/notifications"
INBOX = "/api/me/notifications"


def local_in(minutes):
    return to_local(utcnow() + timedelta(minutes=minutes), local_tz()).strftime("%Y-%m-%dT%H:%M")


def post(client, **overrides):
    body = {"title": "Pengumuman", "body": "Sekolah libur **besok**.", "type": "announcement",
            "audience": "all", "delivery": "now", **overrides}
    return client.post(API, json=body)


def active_count():
    return db.session.scalar(select(func.count(User.id)).where(User.is_active.is_(True)))


def inbox_rows(user_id=None):
    stmt = select(func.count()).select_from(UserNotification)
    if user_id:
        stmt = stmt.where(UserNotification.user_id == user_id)
    return db.session.scalar(stmt)


@pytest.fixture()
def people(make_user):
    """admin (logged in via admin_client) + 2 students + 1 inactive student + 1 content manager."""
    return {
        "s1": make_user("user", email="s1@example.com"),
        "s2": make_user("user", email="s2@example.com"),
        "gone": make_user("user", email="gone@example.com", is_active=False),
        "cm": make_user("content_manager", email="cm@example.com"),
    }


# --- Sending -----------------------------------------------------------------------------

def test_send_now_reaches_all_active_users(admin_client, people):
    data = post(admin_client).get_json()
    assert data["status"] == "sent" and data["recipients"] == active_count() == 4  # test admin + 3 active people
    assert inbox_rows(people["gone"].id) == 0
    assert "<strong>besok</strong>" in data["body_html"]


def test_send_to_role_and_to_one_person(admin_client, people):
    role_id = db.session.scalar(select(Role.id).where(Role.name == "user"))
    by_role = post(admin_client, audience="role", audience_role_id=role_id).get_json()
    assert by_role["recipients"] == 2 and by_role["audience_label"] == "Role: user"
    direct = post(admin_client, audience="user", audience_user_id=people["s1"].id).get_json()
    assert direct["recipients"] == 1 and direct["audience_label"] == people["s1"].full_name


@pytest.mark.parametrize("overrides,field", [
    ({"audience": "role"}, "audience_role_id"),
    ({"audience": "user"}, "audience_user_id"),
    ({"audience": "role", "audience_role_id": 9999}, "audience_role_id"),
    ({"delivery": "schedule"}, "scheduled_at"),
    ({"delivery": "schedule", "scheduled_at": "tomorrow"}, "scheduled_at"),
    ({"delivery": "later"}, "delivery"),
    ({"type": "reminder"}, "type"),
    ({"audience": "everyone"}, "audience"),
    ({"title": "x"}, "title"),
    ({"body": " "}, "body"),
    ({"event_id": 9999}, "event_id"),
])
def test_validation(admin_client, overrides, field):
    response = post(admin_client, **overrides)
    assert response.status_code == 422, response.get_json()
    assert field in response.get_json()["fields"]


def test_cannot_send_to_inactive_person(admin_client, people):
    response = post(admin_client, audience="user", audience_user_id=people["gone"].id)
    assert response.status_code == 422 and "audience_user_id" in response.get_json()["fields"]


def test_schedule_must_be_in_future(admin_client):
    response = post(admin_client, delivery="schedule", scheduled_at=local_in(-30))
    assert response.status_code == 422 and "future" in response.get_json()["fields"]["scheduled_at"]


# --- Scheduling & dispatch ------------------------------------------------------------------

def test_scheduled_notification_is_delivered_once_when_due(admin_client, people):
    data = post(admin_client, delivery="schedule", scheduled_at=local_in(120)).get_json()
    assert data["status"] == "scheduled" and inbox_rows() == 0

    assert service.dispatch_due(now=utcnow())["announcements"] == 0  # not due yet
    stats = service.dispatch_due(now=utcnow() + timedelta(hours=3))
    assert stats["announcements"] == 1 and stats["recipients"] == active_count()
    assert service.dispatch_due(now=utcnow() + timedelta(hours=4))["announcements"] == 0  # no double delivery
    assert inbox_rows() == active_count()
    assert db.session.get(Notification, data["id"]).status == "sent"


def test_cancelled_schedule_is_not_sent_and_can_be_rescheduled(admin_client):
    data = post(admin_client, delivery="schedule", scheduled_at=local_in(60)).get_json()
    assert admin_client.post(f"{API}/{data['id']}/cancel").get_json()["status"] == "cancelled"
    assert service.dispatch_due(now=utcnow() + timedelta(hours=2))["announcements"] == 0
    rescheduled = admin_client.put(f"{API}/{data['id']}", json={
        "title": "Pengumuman", "body": "Baru", "audience": "all", "delivery": "schedule", "scheduled_at": local_in(90)})
    assert rescheduled.get_json()["status"] == "scheduled"


def test_draft_then_send_and_sent_is_locked(admin_client):
    draft = post(admin_client, delivery="draft").get_json()
    assert draft["status"] == "draft" and inbox_rows() == 0
    sent = admin_client.post(f"{API}/{draft['id']}/send").get_json()
    assert sent["status"] == "sent" and sent["recipients"] >= 1
    assert admin_client.post(f"{API}/{draft['id']}/send").status_code == 409
    edit = admin_client.put(f"{API}/{draft['id']}", json={"title": "Changed", "body": "x", "delivery": "draft"})
    assert edit.status_code == 409
    assert admin_client.post(f"{API}/{draft['id']}/cancel").status_code == 409


def test_delete_sent_removes_from_inboxes(admin_client, people):
    data = post(admin_client).get_json()
    assert inbox_rows() == active_count()
    assert admin_client.delete(f"{API}/{data['id']}").status_code == 204
    assert inbox_rows() == 0


def test_inbox_open_delivers_due_items(app, admin_client, client, people):
    data = post(admin_client, delivery="schedule", scheduled_at=local_in(60)).get_json()
    db.session.get(Notification, data["id"]).scheduled_at = utcnow() - timedelta(minutes=1)  # now due
    db.session.commit()
    student = app.test_client()
    login(student, people["s1"].email)
    inbox = student.get(INBOX).get_json()
    assert inbox["unread"] == 1 and inbox["items"][0]["title"] == "Pengumuman"


def test_quiet_dispatch_is_throttled(app):
    service.dispatch_quietly()
    first = service.last_run()["at"]
    service.dispatch_quietly()
    assert service.last_run()["at"] == first


# --- Inbox -------------------------------------------------------------------------------------

def test_inbox_read_flow_and_stats(app, admin_client, people):
    data = post(admin_client).get_json()
    student = app.test_client()
    login(student, people["s1"].email)
    assert student.get(INBOX).get_json()["unread"] == 1
    assert student.post(f"{INBOX}/{data['id']}/read").get_json()["unread"] == 0
    detail = admin_client.get(f"{API}/{data['id']}").get_json()
    assert detail["read"] == 1 and detail["recipients"] == active_count()

    post(admin_client, title="Kedua")
    post(admin_client, title="Ketiga")
    assert student.get(INBOX).get_json()["unread"] == 2
    assert student.post(f"{INBOX}/read-all").get_json()["unread"] == 0


def test_inbox_is_private(app, admin_client, people):
    data = post(admin_client, audience="user", audience_user_id=people["s1"].id).get_json()
    other = app.test_client()
    login(other, people["s2"].email)
    assert other.get(INBOX).get_json()["items"] == []
    other.post(f"{INBOX}/{data['id']}/read")  # can't mark someone else's copy
    row = db.session.scalar(select(UserNotification).where(UserNotification.user_id == people["s1"].id))
    assert row.read_at is None


def test_inbox_requires_login(client):
    assert client.get(INBOX).status_code == 401


# --- Event reminders -------------------------------------------------------------------------

def make_event(client, start_minutes, reminders, status="upcoming"):
    response = client.post("/api/admin/events", json={
        "title": "Ujian Fisika", "start": local_in(start_minutes), "location": "Lab", "status": status,
        "reminders": reminders})
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_due_reminder_is_delivered_once(admin_client, people):
    event = make_event(admin_client, 30, [{"offset_minutes": 60}])  # fired 30 min ago, event not started
    stats = service.dispatch_due()
    assert stats["reminders"] == 1
    reminder = db.session.scalar(select(Notification).where(Notification.type == "reminder"))
    assert reminder.event_id == event["id"] and reminder.title == "Reminder: Ujian Fisika" and "Lab" in reminder.body
    assert inbox_rows() == active_count()
    assert service.dispatch_due()["reminders"] == 0


def test_future_reminder_waits(admin_client):
    make_event(admin_client, 600, [{"offset_minutes": 60}])
    assert service.dispatch_due()["reminders"] == 0
    assert db.session.scalar(select(EventReminder.is_sent)) is False


def test_late_reminder_is_skipped_not_delivered(admin_client):
    make_event(admin_client, -120, [{"offset_minutes": 60}])
    stats = service.dispatch_due()
    assert stats["expired_reminders"] == 1 and stats["reminders"] == 0 and inbox_rows() == 0


def test_start_time_reminder_has_grace_period(admin_client):
    make_event(admin_client, -10, [{"offset_minutes": 0}])
    assert service.dispatch_due()["reminders"] == 1


def test_cancelled_event_sends_no_reminder(admin_client):
    make_event(admin_client, 30, [{"offset_minutes": 60}], status="cancelled")
    assert service.dispatch_due()["reminders"] == 0


def test_email_reminder_without_smtp_falls_back_to_in_app(admin_client, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    make_event(admin_client, 30, [{"offset_minutes": 60, "channel": "email"}])
    stats = service.dispatch_due()
    assert stats["reminders"] == 1 and stats["emails_skipped"] == 1 and inbox_rows() == active_count()


def test_email_reminder_with_smtp(admin_client, people, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "school@example.test")
    sent = {}
    monkeypatch.setattr(service, "send_emails", lambda to, subject, text: sent.update(to=to, subject=subject) or (len(to), 0))
    make_event(admin_client, 30, [{"offset_minutes": 60, "channel": "email"}])
    stats = service.dispatch_due()
    assert stats["emails_sent"] == active_count() == 4 and "s1@example.com" in sent["to"] and "gone@example.com" not in sent["to"]


def test_reminders_tab_and_summary(admin_client):
    make_event(admin_client, 24 * 60, [{"offset_minutes": 60}, {"offset_minutes": 1440}])
    items = admin_client.get(f"{API}/reminders").get_json()["items"]
    assert len(items) == 2 and items[0]["offset_minutes"] == 1440  # sorted by send time
    summary = admin_client.get(f"{API}/summary").get_json()
    assert summary["pending_reminders"] == 2 and summary["email_configured"] in (True, False)


def test_reminders_hidden_from_default_list(admin_client):
    make_event(admin_client, 30, [{"offset_minutes": 60}])
    service.dispatch_due()
    assert admin_client.get(API).get_json()["total"] == 0
    assert admin_client.get(f"{API}?type=reminder").get_json()["total"] == 1


# --- Manual trigger, CLI, permissions, page --------------------------------------------------------

def test_process_due_now_button(admin_client):
    make_event(admin_client, 30, [{"offset_minutes": 60}])
    assert admin_client.post(f"{API}/dispatch").get_json()["reminders"] == 1


def test_cli_dispatch(app, admin_client):
    make_event(admin_client, 30, [{"offset_minutes": 60}])
    result = app.test_cli_runner().invoke(args=["notifications", "dispatch"])
    assert result.exit_code == 0 and "reminders: 1" in result.output


def test_permissions(app, client, people):
    login(client, people["cm"].email)  # content manager: notifications.write
    assert post(client).status_code == 201
    viewer = app.test_client()
    login(viewer, db.session.scalar(select(User.email).where(User.id == people["s1"].id)))  # regular user
    assert viewer.get(API).status_code == 403
    assert viewer.get(INBOX).status_code == 200


def test_viewer_can_read_not_send(client, make_user):
    login(client, make_user("viewer").email)
    assert client.get(API).status_code == 200
    assert post(client).status_code == 403
    assert client.post(f"{API}/dispatch").status_code == 403
    assert client.get(f"{API}/recipients").status_code == 403


def test_recipient_search(admin_client, people):
    found = admin_client.get(f"{API}/recipients?q=s1@").get_json()
    assert [p["email"] for p in found] == ["s1@example.com"]
    assert "gone@example.com" not in [p["email"] for p in admin_client.get(f"{API}/recipients?q=gone").get_json()]


def test_page_and_bell(admin_client):
    page = admin_client.get("/admin/notifications")
    assert page.status_code == 200 and b"Process due now" in page.data and b"data-inbox" in page.data
