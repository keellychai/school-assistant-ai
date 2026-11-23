"""Idempotent database seeding.

* Roles and permissions are synced every run (the definitions live in code).
* Settings and sample content are only INSERTED when missing, so running
  `flask seed` again never overwrites changes made in the Admin Control Panel.
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .extensions import db
from .models import (
    AnswerTemplate,
    AssistantSettings,
    ConfiguredAnswer,
    ConfiguredQuestion,
    ConversationFlow,
    ConversationFlowStep,
    Event,
    FollowUpQuestion,
    KnowledgeCategory,
    KnowledgeEntry,
    Permission,
    QuestionCategory,
    Role,
    SystemSetting,
    User,
)
from .models.base import utcnow

SEED_FILE = Path(__file__).parent / "seed_data" / "school_data.json"
DEFAULT_TIMEZONE = "Asia/Jakarta"

PERMISSIONS = {
    "dashboard.view": "View the admin dashboard",
    "events.read": "View events", "events.write": "Create, edit and delete events",
    "notifications.read": "View notifications", "notifications.write": "Create, edit, send notifications",
    "chatconfig.read": "View AI Chat configuration", "chatconfig.write": "Edit questions, answers and flows",
    "assistant.read": "View assistant settings", "assistant.write": "Edit assistant settings",
    "knowledge.read": "View knowledge base", "knowledge.write": "Edit knowledge base",
    "monitoring.read": "View conversations and analytics",
    "users.read": "View users", "users.write": "Edit and (de)activate users",
    "roles.manage": "Create roles and assign permissions",
    "settings.read": "View system settings", "settings.write": "Edit system settings",
    "activity.read": "View activity logs",
}

_ALL = set(PERMISSIONS)
_READS = {p for p in PERMISSIONS if p.endswith(".read")} | {"dashboard.view"}

ROLES = {
    "super_admin": ("Full access, including roles and security settings", _ALL),
    "admin": ("Manages every module except roles", _ALL - {"roles.manage"}),
    "content_manager": (
        "Manages events, notifications, Q&A and knowledge",
        {"dashboard.view", "events.read", "events.write", "notifications.read", "notifications.write",
         "chatconfig.read", "chatconfig.write", "knowledge.read", "knowledge.write",
         "assistant.read", "monitoring.read"},
    ),
    "viewer": ("Read-only access to the admin panel", _READS - {"users.read", "settings.read", "activity.read"}),
    "user": ("Regular user: chat only, no admin access", set()),
}

DEFAULT_SYSTEM_SETTINGS = {
    "app.name": "SchoolSync AI",
    "app.tagline": "AI-Based School Administration and Communication Assistant",
    "app.logo_url": None,
    "theme.mode": "light",
    "theme.primary_color": "#4f63df",
    "locale.default_language": "id",
    "locale.timezone": DEFAULT_TIMEZONE,
    "security.session_timeout_minutes": 480,
    "security.max_failed_logins": 5,
    "security.lockout_minutes": 15,
    "security.password_min_length": 8,
}


def seed_roles():
    permissions = {p.code: p for p in db.session.scalars(select(Permission))}
    for code, description in PERMISSIONS.items():
        if code not in permissions:
            permissions[code] = Permission(code=code, description=description)
            db.session.add(permissions[code])

    for name, (description, codes) in ROLES.items():
        role = db.session.scalar(select(Role).where(Role.name == name))
        if role is None:
            role = Role(name=name, description=description, is_system=True)
            db.session.add(role)
        role.permissions = [permissions[c] for c in sorted(codes)]
    db.session.flush()


def seed_settings():
    AssistantSettings.get()
    for key, value in DEFAULT_SYSTEM_SETTINGS.items():
        if db.session.get(SystemSetting, key) is None:
            db.session.add(SystemSetting(key=key, value=value))
    db.session.flush()


def create_admin(email, password, full_name="Administrator", role_name="super_admin"):
    """Create an admin user, or return the existing one with that email (unchanged)."""
    email = email.strip().lower()
    user = db.session.scalar(select(User).where(User.email == email))
    if user is not None:
        return user, False
    role = db.session.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        raise ValueError(f"Role '{role_name}' does not exist. Run `flask seed` first.")
    user = User(email=email, full_name=full_name, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    return user, True


def _local_to_utc(value, tz):
    if not value:
        return None
    local = datetime.fromisoformat(value).replace(tzinfo=tz)
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def seed_sample_content():
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    tz = ZoneInfo(SystemSetting.get_value("locale.timezone", DEFAULT_TIMEZONE))
    counts = {}

    # Knowledge
    kcats = {c.slug: c for c in db.session.scalars(select(KnowledgeCategory))}
    for item in data["knowledge_categories"]:
        if item["slug"] not in kcats:
            kcats[item["slug"]] = KnowledgeCategory(**item)
            db.session.add(kcats[item["slug"]])
    existing_refs = set(db.session.scalars(select(KnowledgeEntry.external_ref)))
    counts["knowledge_entries"] = 0
    for item in data["knowledge_entries"]:
        if item["ref"] in existing_refs:
            continue
        db.session.add(KnowledgeEntry(
            external_ref=item["ref"], category=kcats[item["category"]], title=item["title"],
            content_markdown=item["content"], keywords=item["keywords"], source_ref="Sample school data",
        ))
        counts["knowledge_entries"] += 1

    # Events (only when the table is empty, since events have no natural unique key)
    counts["events"] = 0
    if db.session.scalar(select(Event.id).limit(1)) is None:
        now = utcnow()
        for item in data["events"]:
            start, end = _local_to_utc(item["start"], tz), _local_to_utc(item["end"], tz)
            status = "completed" if (end or start) < now else "upcoming"
            db.session.add(Event(
                title=item["title"], category=item["category"], start_at=start, end_at=end,
                location=item["location"], description=item["description"], status=status,
            ))
            counts["events"] += 1

    # Answer templates
    for item in data["answer_templates"]:
        if db.session.scalar(select(AnswerTemplate).where(AnswerTemplate.name == item["name"])) is None:
            db.session.add(AnswerTemplate(name=item["name"], description=item["description"],
                                          body_markdown=item["body"]))

    # Q&A
    qcats = {c.slug: c for c in db.session.scalars(select(QuestionCategory))}
    for item in data["question_categories"]:
        if item["slug"] not in qcats:
            qcats[item["slug"]] = QuestionCategory(slug=item["slug"], name=item["name"],
                                                   display_order=item["order"])
            db.session.add(qcats[item["slug"]])
    db.session.flush()

    questions, counts["questions"] = {}, 0
    for item in data["questions"]:
        category = qcats[item["category"]]
        question = db.session.scalar(select(ConfiguredQuestion).where(
            ConfiguredQuestion.category_id == category.id, ConfiguredQuestion.text == item["text"]))
        if question is None:
            question = ConfiguredQuestion(
                category=category, text=item["text"], keywords=item["keywords"],
                is_suggested=item["suggested"], display_order=item["order"],
            )
            question.answers.append(ConfiguredAnswer(body_markdown=item["answer"]))
            db.session.add(question)
            counts["questions"] += 1
        questions[item["key"]] = question
    db.session.flush()

    for item in data["questions"]:
        parent = questions[item["key"]]
        linked = {link.child_question_id for link in parent.follow_up_links}
        for order, child_key in enumerate(item.get("follow_ups", []), start=1):
            child = questions[child_key]
            if child.id not in linked:
                parent.follow_up_links.append(FollowUpQuestion(child=child, display_order=order))

    for item in data["flows"]:
        if db.session.scalar(select(ConversationFlow).where(ConversationFlow.name == item["name"])) is None:
            flow = ConversationFlow(name=item["name"], description=item["description"],
                                    entry_question=questions[item["entry"]])
            root = ConversationFlowStep(question=questions[item["entry"]], step_order=0)
            flow.steps.append(root)
            for order, key in enumerate(item["children"], start=1):
                child = ConversationFlowStep(question=questions[key], step_order=order, parent=root)
                flow.steps.append(child)
            db.session.add(flow)

    db.session.flush()
    return counts


def seed_all(sample=True):
    seed_roles()
    seed_settings()
    counts = seed_sample_content() if sample else {}
    db.session.commit()
    return counts
