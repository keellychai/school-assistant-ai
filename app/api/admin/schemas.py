"""Request bodies for the admin API, validated server-side with pydantic.

Field names match the form field names in the admin pages, so validation
errors can be shown next to the right input.
"""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from ...services.markdown_render import is_safe_url
from .utils import FieldErrors

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
Slug = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
Order = Annotated[int, Field(ge=0, le=100000)]

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_FLOW_DEPTH = 6
MAX_FLOW_STEPS = 60


def clean_keywords(value):
    """Trim, lower-case and de-duplicate keywords (max 25, 60 characters each)."""
    cleaned = []
    for keyword in value:
        keyword = " ".join(str(keyword).split()).lower()
        if not keyword:
            continue
        if len(keyword) > 60:
            raise ValueError("Each keyword must be at most 60 characters.")
        if keyword not in cleaned:
            cleaned.append(keyword)
    if len(cleaned) > 25:
        raise ValueError("Use at most 25 keywords.")
    return cleaned


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _optional_text(value, limit):
    if value is None:
        return None
    value = str(value).strip()
    if len(value) > limit:
        raise ValueError(f"Must be at most {limit} characters.")
    return value or None


# --- Categories -----------------------------------------------------------------

class CategoryIn(Strict):
    name: Name
    slug: Slug = ""
    description: str | None = None
    display_order: Order = 0
    is_enabled: bool = True

    @field_validator("slug")
    @classmethod
    def _slug(cls, value):
        value = value.lower()
        if value and not SLUG_RE.match(value):
            raise ValueError("Use lowercase letters, numbers and single hyphens (e.g. school-events).")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value):
        return _optional_text(value, 1000)


class ReorderIn(Strict):
    ids: list[int] = Field(max_length=1000)

    @field_validator("ids")
    @classmethod
    def _unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("The list contains duplicates.")
        return value


# --- Questions ------------------------------------------------------------------

class QuestionIn(Strict):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
    category_id: int | None = None
    keywords: list[str] = Field(default_factory=list)
    is_suggested: bool = False
    display_order: Order = 0
    is_enabled: bool = True

    @field_validator("category_id")
    @classmethod
    def _category(cls, value):
        if value is None:
            raise ValueError("Choose a category.")
        return value

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value):
        return clean_keywords(value)


class QuestionCreate(QuestionIn):
    # Optional first answer, so a usable question can be created in one step.
    answer: str | None = None

    @field_validator("answer")
    @classmethod
    def _answer(cls, value):
        return _optional_text(value, 20000)


class QuestionPatch(Strict):
    is_enabled: bool | None = None
    is_suggested: bool | None = None


class FollowUpsIn(Strict):
    question_ids: list[int] = Field(max_length=12)

    @field_validator("question_ids")
    @classmethod
    def _unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("A follow-up question can only be added once.")
        return value


# --- Answers ------------------------------------------------------------------

class LinkIn(Strict):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

    @field_validator("url")
    @classmethod
    def _url(cls, value):
        if not is_safe_url(value):
            raise ValueError("Use a full http(s):// address, mailto:, tel:, or a path starting with /.")
        return value


class AnswerIn(Strict):
    body_markdown: LongText
    links: list[LinkIn] = Field(default_factory=list, max_length=10)
    template_id: int | None = None
    priority: Annotated[int, Field(ge=-1000, le=1000)] = 0
    is_enabled: bool = True


class AnswerPatch(Strict):
    is_enabled: bool


class PreviewIn(Strict):
    body_markdown: Annotated[str, StringConstraints(max_length=20000)] = ""
    template_id: int | None = None


# --- Templates ------------------------------------------------------------------

class TemplateIn(Strict):
    name: Name
    description: str | None = None
    body_markdown: LongText

    @field_validator("description")
    @classmethod
    def _description(cls, value):
        return _optional_text(value, 255)

    @field_validator("body_markdown")
    @classmethod
    def _placeholder(cls, value):
        if "{answer}" not in value:
            raise ValueError("Include {answer} where the answer text should appear.")
        return value


# --- Conversation flows -----------------------------------------------------------

class FlowStepIn(Strict):
    question_id: int
    children: list["FlowStepIn"] = Field(default_factory=list, max_length=12)


class FlowIn(Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    description: str | None = None
    is_enabled: bool = True
    root: FlowStepIn | None = None

    @field_validator("description")
    @classmethod
    def _description(cls, value):
        return _optional_text(value, 2000)

    @field_validator("root")
    @classmethod
    def _tree_size(cls, root):
        if root is None:
            return root
        count = 0

        def walk(step, depth):
            nonlocal count
            count += 1
            if depth > MAX_FLOW_DEPTH:
                raise ValueError(f"A flow can be at most {MAX_FLOW_DEPTH} levels deep.")
            for child in step.children:
                walk(child, depth + 1)

        walk(root, 1)
        if count > MAX_FLOW_STEPS:
            raise ValueError(f"A flow can have at most {MAX_FLOW_STEPS} steps.")
        return root


# --- Assistant settings ------------------------------------------------------------

class FeaturesIn(Strict):
    suggested_questions: bool = True
    follow_up_buttons: bool = True
    show_sources: bool = True
    chat_history: bool = True
    use_knowledge_base: bool = True
    ai_use_knowledge_context: bool = True
    ai_history_messages: Annotated[int, Field(ge=0, le=50)] = 10


class AssistantSettingsIn(Strict):
    assistant_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
    welcome_message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    initial_greeting: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""
    default_language: str
    tone: str
    personality: str | None = None
    fallback_answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    match_threshold: Annotated[float, Field(ge=0.1, le=1.0)] = 0.6
    features: FeaturesIn = Field(default_factory=FeaturesIn)

    ai_enabled: bool = False
    ai_provider: str
    ai_model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    api_key_env_var: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    ai_effort: str = "medium"
    max_tokens: Annotated[int, Field(ge=1024, le=64000)] = 16000

    @field_validator("default_language")
    @classmethod
    def _language(cls, value):
        from ...services.assistant_config import LANGUAGES
        if value not in {l["id"] for l in LANGUAGES}:
            raise ValueError("Choose a supported language.")
        return value

    @field_validator("tone")
    @classmethod
    def _tone(cls, value):
        from ...services.assistant_config import TONES
        if value not in {t["id"] for t in TONES}:
            raise ValueError("Choose a tone from the list.")
        return value

    @field_validator("personality")
    @classmethod
    def _personality(cls, value):
        return _optional_text(value, 1000)

    @field_validator("ai_provider")
    @classmethod
    def _provider(cls, value):
        from ...services.assistant_config import PROVIDERS
        if value not in PROVIDERS:
            raise ValueError("Choose a supported provider.")
        return value

    @field_validator("ai_effort")
    @classmethod
    def _effort(cls, value):
        from ...services.assistant_config import EFFORT_IDS
        if value not in EFFORT_IDS:
            raise ValueError("Choose an effort level from the list.")
        return value

    @field_validator("api_key_env_var")
    @classmethod
    def _key_env(cls, value):
        from ...services.assistant_config import KEY_ENV_PATTERN
        if not KEY_ENV_PATTERN.match(value):
            raise ValueError("Use an environment variable name ending in _API_KEY, e.g. ANTHROPIC_API_KEY.")
        return value

    @field_validator("ai_model")
    @classmethod
    def _model(cls, value, info):
        from ...services.assistant_config import PROVIDERS
        provider = PROVIDERS.get(info.data.get("ai_provider"))
        if provider and not re.match(provider["model_pattern"], value):
            raise ValueError("Enter a valid model ID for this provider, e.g. claude-opus-5-5.")
        return value


# --- Events ---------------------------------------------------------------------------

EVENT_STATUS_IDS = ("upcoming", "ongoing", "completed", "cancelled")
CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9 \-]{0,48}[a-z0-9]$|^[a-z0-9]$")
MAX_REMINDER_MINUTES = 30 * 24 * 60


class ReminderIn(Strict):
    offset_minutes: Annotated[int, Field(ge=0, le=MAX_REMINDER_MINUTES)]
    channel: str = "in_app"

    @field_validator("channel")
    @classmethod
    def _channel(cls, value):
        if value not in ("in_app", "email"):
            raise ValueError("Choose in-app or email.")
        return value


class EventIn(Strict):
    """Times are LOCAL wall-clock values (school timezone): 'YYYY-MM-DDTHH:MM',
    or 'YYYY-MM-DD' when all_day is true. The API converts them to UTC."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
    description: str | None = None
    location: str | None = None
    category: str = "general"
    status: str = "upcoming"
    all_day: bool = False
    start: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    end: str | None = None
    reminders: list[ReminderIn] = Field(default_factory=list, max_length=5)

    @field_validator("description")
    @classmethod
    def _description(cls, value):
        return _optional_text(value, 5000)

    @field_validator("location")
    @classmethod
    def _location(cls, value):
        return _optional_text(value, 200)

    @field_validator("category")
    @classmethod
    def _category(cls, value):
        value = " ".join((value or "general").lower().split())
        if not CATEGORY_RE.match(value):
            raise ValueError("Use letters, numbers, spaces or hyphens (max 50 characters).")
        return value

    @field_validator("status")
    @classmethod
    def _status(cls, value):
        if value not in EVENT_STATUS_IDS:
            raise ValueError("Choose a status from the list.")
        return value

    @field_validator("end")
    @classmethod
    def _end(cls, value):
        return (value or "").strip() or None

    @staticmethod
    def _parse(value, all_day, field):
        from datetime import date, datetime
        try:
            if all_day:
                return datetime.combine(date.fromisoformat(value[:10]), datetime.min.time())
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{field}: enter a valid {'date' if all_day else 'date and time'}.") from None
        if parsed.tzinfo is not None:
            raise ValueError(f"{field}: send local time without a timezone offset.")
        return parsed.replace(second=0, microsecond=0)

    def start_local(self):
        return self._parse(self.start, self.all_day, "start")

    def end_local(self):
        return self._parse(self.end, self.all_day, "end") if self.end else None

    @model_validator(mode="after")
    def _check(self):
        errors = {}
        try:
            start = self.start_local()
        except ValueError as error:
            errors["start"] = str(error).split(": ", 1)[1]
            start = None
        try:
            end = self.end_local()
        except ValueError as error:
            errors["end"] = str(error).split(": ", 1)[1]
            end = None
        if start and end and end < start:
            errors["end"] = "The end must be after the start."
        if start and not 2000 <= start.year <= 2100:
            errors["start"] = "Enter a year between 2000 and 2100."
        pairs = [(r.offset_minutes, r.channel) for r in self.reminders]
        if len(set(pairs)) != len(pairs):
            errors["reminders"] = "The same reminder is listed twice."
        if errors:
            # Raised as a dict so parse_body can map each message to its field.
            raise FieldErrors(errors)
        return self


class EventStatusIn(Strict):
    status: str

    @field_validator("status")
    @classmethod
    def _status(cls, value):
        if value not in EVENT_STATUS_IDS:
            raise ValueError("Choose a status from the list.")
        return value


# --- Notifications ----------------------------------------------------------------------

class NotificationIn(Strict):
    """scheduled_at is LOCAL time ('YYYY-MM-DDTHH:MM'), required when delivery is 'schedule'."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
    type: str = "announcement"
    audience: str = "all"
    audience_role_id: int | None = None
    audience_user_id: int | None = None
    delivery: str = "now"            # now | schedule | draft
    scheduled_at: str | None = None
    event_id: int | None = None

    @field_validator("type")
    @classmethod
    def _type(cls, value):
        if value not in ("announcement", "system"):
            raise ValueError("Choose announcement or system message.")
        return value

    @field_validator("audience")
    @classmethod
    def _audience(cls, value):
        if value not in ("all", "role", "user"):
            raise ValueError("Choose who receives it.")
        return value

    @field_validator("delivery")
    @classmethod
    def _delivery(cls, value):
        if value not in ("now", "schedule", "draft"):
            raise ValueError("Choose send now, schedule or save as draft.")
        return value

    def scheduled_local(self):
        from datetime import datetime
        return datetime.fromisoformat(self.scheduled_at).replace(second=0, microsecond=0) if self.scheduled_at else None

    @model_validator(mode="after")
    def _check(self):
        errors = {}
        if self.audience == "role" and not self.audience_role_id:
            errors["audience_role_id"] = "Choose a role."
        if self.audience == "user" and not self.audience_user_id:
            errors["audience_user_id"] = "Choose a person."
        self.scheduled_at = (self.scheduled_at or "").strip() or None
        if self.delivery == "schedule":
            if not self.scheduled_at:
                errors["scheduled_at"] = "Choose when to send it."
            else:
                try:
                    parsed = self.scheduled_local()
                    if "+" in self.scheduled_at[10:] or self.scheduled_at.endswith("Z"):
                        raise ValueError
                    if not 2000 <= parsed.year <= 2100:
                        raise ValueError
                except ValueError:
                    errors["scheduled_at"] = "Enter a valid date and time."
        if errors:
            raise FieldErrors(errors)
        return self


# --- Knowledge base ------------------------------------------------------------------

REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,49}$")


class KnowledgeCategoryIn(Strict):
    name: Name
    slug: Slug = ""
    description: str | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, value):
        value = value.lower()
        if value and not SLUG_RE.match(value):
            raise ValueError("Use lowercase letters, numbers and single hyphens (e.g. school-events).")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value):
        return _optional_text(value, 1000)


class KnowledgeEntryIn(Strict):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
    category_id: int | None = None
    external_ref: str | None = None
    content_markdown: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50000)]
    keywords: list[str] = Field(default_factory=list)
    source_ref: str | None = None
    available_to_ai: bool = True
    is_published: bool = True

    @field_validator("category_id")
    @classmethod
    def _category(cls, value):
        if value is None:
            raise ValueError("Choose a category.")
        return value

    @field_validator("external_ref")
    @classmethod
    def _ref(cls, value):
        value = (value or "").strip() or None
        if value and not REF_RE.match(value):
            raise ValueError("Use up to 50 letters, numbers, dots, dashes or underscores (e.g. SCH012).")
        return value.upper() if value else None

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value):
        return clean_keywords(value)

    @field_validator("source_ref")
    @classmethod
    def _source(cls, value):
        return _optional_text(value, 500)


class KnowledgePatch(Strict):
    is_published: bool | None = None
    available_to_ai: bool | None = None


class TestMatchIn(Strict):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


# --- Users & roles -----------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,48}$")
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]


def _email(value):
    value = (value or "").strip().lower()
    if len(value) > 255 or not EMAIL_RE.match(value):
        raise ValueError("Enter a valid email address.")
    return value


class UserUpdateIn(Strict):
    full_name: FullName
    email: str
    role_id: int | None = None

    @field_validator("email")
    @classmethod
    def _check_email(cls, value):
        return _email(value)

    @field_validator("role_id")
    @classmethod
    def _role(cls, value):
        if value is None:
            raise ValueError("Choose a role.")
        return value


class UserCreateIn(UserUpdateIn):
    # Leave empty to generate a temporary password (shown once to the admin).
    password: str | None = None
    is_active: bool = True

    @field_validator("password")
    @classmethod
    def _password(cls, value):
        value = value or None
        if value is not None and len(value) > 256:
            raise ValueError("Use at most 256 characters.")
        return value


class UserStatusIn(Strict):
    is_active: bool


class PasswordResetIn(Strict):
    password: str | None = None  # empty = generate a temporary one

    @field_validator("password")
    @classmethod
    def _password(cls, value):
        value = value or None
        if value is not None and len(value) > 256:
            raise ValueError("Use at most 256 characters.")
        return value


class RoleIn(Strict):
    name: str
    description: str | None = None
    permissions: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        value = "_".join((value or "").strip().lower().split())
        if not ROLE_NAME_RE.match(value):
            raise ValueError("Use lowercase letters, numbers and underscores, starting with a letter (e.g. teacher).")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value):
        return _optional_text(value, 255)

    @field_validator("permissions")
    @classmethod
    def _unique(cls, value):
        return sorted(set(value))


# --- Fallback -------------------------------------------------------------------

class FallbackIn(Strict):
    fallback_answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


# --- System settings ---------------------------------------------------------------

class SystemGeneralIn(Strict):
    app_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]
    app_tagline: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)] = ""
    theme_mode: str
    primary_color: str
    default_language: str
    timezone: str

    @field_validator("theme_mode")
    @classmethod
    def _theme(cls, value):
        from ...services.system_settings import THEME_MODES
        if value not in {m["id"] for m in THEME_MODES}:
            raise ValueError("Choose light, dark or follow each device.")
        return value

    @field_validator("primary_color")
    @classmethod
    def _color(cls, value):
        from ...services.system_settings import HEX_COLOR
        value = (value or "").strip().lower()
        if not HEX_COLOR.match(value):
            raise ValueError("Use a hex colour like #4f63df.")
        return value

    @field_validator("default_language")
    @classmethod
    def _language(cls, value):
        from ...services.system_settings import LANGUAGES
        if value not in {l["id"] for l in LANGUAGES}:
            raise ValueError("Choose a supported language.")
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value):
        from ...services.system_settings import timezones
        if value not in timezones():
            raise ValueError("Choose a timezone from the list.")
        return value


def _bounded(key):
    from ...services.system_settings import SECURITY_LIMITS
    _, low, high = SECURITY_LIMITS[key]
    return Annotated[int, Field(ge=low, le=high)]


class SystemSecurityIn(Strict):
    session_timeout_minutes: _bounded("security.session_timeout_minutes")
    max_failed_logins: _bounded("security.max_failed_logins")
    lockout_minutes: _bounded("security.lockout_minutes")
    password_min_length: _bounded("security.password_min_length")


# --- My profile -------------------------------------------------------------------------

class ProfileIn(Strict):
    full_name: FullName
    email: str
    # Required only when the email changes (checked in the view).
    current_password: str | None = Field(default=None, max_length=256)

    @field_validator("email")
    @classmethod
    def _check_email(cls, value):
        return _email(value)


class PasswordChangeIn(Strict):
    current_password: Annotated[str, Field(max_length=256)]
    new_password: Annotated[str, Field(max_length=256)]
    confirm_password: Annotated[str, Field(max_length=256)]

    @model_validator(mode="after")
    def _match(self):
        errors = {}
        if not self.current_password:
            errors["current_password"] = "Enter your current password."
        if self.new_password != self.confirm_password:
            errors["confirm_password"] = "The passwords don't match."
        if self.new_password and self.new_password == self.current_password:
            errors["new_password"] = "Choose a password different from your current one."
        if errors:
            raise FieldErrors(errors)
        return self
