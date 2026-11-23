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


# --- Fallback -------------------------------------------------------------------

class FallbackIn(Strict):
    fallback_answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
