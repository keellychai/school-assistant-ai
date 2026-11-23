"""Single source of truth for the admin sidebar, breadcrumbs and module pages.

Each item is shown only to users holding its permission, and its page route is
protected by the same permission (see routes.py), so the menu and the access
rules can never drift apart.
"""

from dataclasses import dataclass

from flask_login import current_user


@dataclass(frozen=True)
class NavItem:
    endpoint: str          # admin.<endpoint>
    path: str              # URL under /admin
    label: str
    icon: str
    permission: str
    description: str = ""
    planned_step: int | None = None  # set while the module is still a placeholder


@dataclass(frozen=True)
class NavSection:
    title: str
    items: tuple[NavItem, ...]


NAVIGATION = (
    NavSection("Overview", (
        NavItem("index", "/", "Dashboard", "grid", "dashboard.view",
                "Key numbers and recent activity."),
    )),
    NavSection("Operations", (
        NavItem("events", "/events", "Events", "calendar", "events.read",
                "Create and manage events and schedules, with calendar and list views."),
        NavItem("notifications", "/notifications", "Notifications", "bell", "notifications.read",
                "Announcements, scheduled notifications and event reminders."),
    )),
    NavSection("AI Assistant", (
        NavItem("questions", "/chat/questions", "Questions & answers", "help-circle", "chatconfig.read",
                "Questions, answers, keywords, suggested questions and follow-ups shown in AI Chat."),
        NavItem("question_categories", "/chat/categories", "Categories", "folder", "chatconfig.read",
                "Group questions into categories shown in AI Chat."),
        NavItem("flows", "/chat/flows", "Conversation flows", "git-branch", "chatconfig.read",
                "Predefined conversation paths built from configured questions."),
        NavItem("answer_templates", "/chat/templates", "Answer templates", "file-text", "chatconfig.read",
                "Reusable answer templates."),
        NavItem("assistant", "/assistant", "Assistant settings", "zap", "assistant.read",
                "Name, greeting, tone, fallback answer and AI provider."),
        NavItem("knowledge", "/knowledge", "Knowledge base", "book-open", "knowledge.read",
                "Information the assistant can use to answer questions.", 8),
        NavItem("monitoring", "/conversations", "Conversations", "eye", "monitoring.read",
                "Conversation history, unanswered and frequent questions.", 12),
    )),
    NavSection("Administration", (
        NavItem("users", "/users", "Users", "users", "users.read",
                "Accounts, profiles, activation and roles.", 9),
        NavItem("roles", "/roles", "Roles & permissions", "shield", "roles.manage",
                "Define roles and what each one may do.", 9),
        NavItem("settings", "/settings", "System settings", "sliders", "settings.read",
                "Branding, theme, language, timezone and security.", 10),
        NavItem("activity", "/activity", "Activity log", "activity", "activity.read",
                "Audit trail of sign-ins and administrative changes."),
    )),
)

ALL_ITEMS = {item.endpoint: (section, item) for section in NAVIGATION for item in section.items}

# Pages that aren't in the menu themselves but belong to a menu item
# (highlighted in the sidebar and shown in the breadcrumb under it).
SUBPAGES = {
    "question_edit": "questions",
    "flow_edit": "flows",
    "flow_new": "flows",
}


def visible_navigation(user=None):
    user = user or current_user
    sections = []
    for section in NAVIGATION:
        items = tuple(i for i in section.items if user.has_permission(i.permission))
        if items:
            sections.append(NavSection(section.title, items))
    return sections


def nav_lookup(endpoint):
    """(section, item) for 'admin.xyz', or (None, None)."""
    if not endpoint or not endpoint.startswith("admin."):
        return None, None
    name = endpoint.split(".", 1)[1]
    return ALL_ITEMS.get(SUBPAGES.get(name, name), (None, None))
