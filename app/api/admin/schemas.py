"""Request bodies for the admin API, validated server-side with pydantic.

Field names match the form field names in the admin pages, so validation
errors can be shown next to the right input.
"""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from ...services.markdown_render import is_safe_url

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
Slug = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
Order = Annotated[int, Field(ge=0, le=100000)]

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_FLOW_DEPTH = 6
MAX_FLOW_STEPS = 60


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


# --- Fallback -------------------------------------------------------------------

class FallbackIn(Strict):
    fallback_answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
