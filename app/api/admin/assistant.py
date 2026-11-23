"""Assistant Settings API: identity, personality, chat features, answer
behaviour and the AI provider. The API key is never stored or returned; only
the name of the server environment variable and whether it is set.
"""

from flask import jsonify, request
from flask_login import current_user
from sqlalchemy import select

from ...auth.security import permission_required
from ...extensions import db
from ...models import AssistantSettings, ConfiguredQuestion, QuestionCategory
from ...services.assistant_config import (
    EFFORT_LEVELS,
    FEATURE_LABELS,
    LANGUAGES,
    PROVIDERS,
    TONES,
    api_key_present,
    features_with_defaults,
    model_label,
)
from ...services.audit import log_activity
from ...services.uploads import UploadError, delete_upload, save_image
from . import bp
from .schemas import AssistantSettingsIn
from .utils import ApiError, iso, parse_body

READ = "assistant.read"
WRITE = "assistant.write"

FIELDS = ("assistant_name", "avatar_url", "welcome_message", "initial_greeting", "default_language", "tone",
          "personality", "fallback_answer", "match_threshold", "features", "ai_enabled", "ai_provider",
          "ai_model", "api_key_env_var", "ai_effort", "max_tokens")


def _snapshot(settings):
    return {field: getattr(settings, field) for field in FIELDS}


def status_json(settings):
    key_present = api_key_present(settings.api_key_env_var)
    if not settings.ai_enabled:
        state, message = "off", "AI answers are off. Only configured questions, the knowledge base and the fallback answer are used."
    elif key_present:
        state, message = "ready", (f"AI answers are on using {model_label(settings.ai_provider, settings.ai_model)}. "
                                   f"The key in {settings.api_key_env_var} is set on the server.")
    else:
        state, message = "missing_key", (f"AI answers are on, but {settings.api_key_env_var} is not set on the server. "
                                         "Until it is, questions that don't match will get the fallback answer.")
    return {"state": state, "message": message, "api_key_present": key_present}


def settings_json(settings):
    return {
        **_snapshot(settings),
        "features": features_with_defaults(settings.features),
        "updated_at": iso(settings.updated_at),
        "updated_by": settings_updated_by(settings),
    }


def settings_updated_by(settings):
    if not settings.updated_by_id:
        return None
    from ...models import User
    user = db.session.get(User, settings.updated_by_id)
    return user.full_name if user else None


def preview_questions():
    """Suggested questions as AI Chat will show them (enabled, in an enabled category, with an answer)."""
    rows = db.session.scalars(
        select(ConfiguredQuestion).join(ConfiguredQuestion.category)
        .where(ConfiguredQuestion.is_suggested.is_(True), ConfiguredQuestion.is_enabled.is_(True),
               QuestionCategory.is_enabled.is_(True))
        .order_by(ConfiguredQuestion.display_order, ConfiguredQuestion.id)
    ).all()
    return [q.text for q in rows if q.active_answer is not None]


@bp.get("/assistant")
@permission_required(READ)
def get_assistant():
    settings = AssistantSettings.get()
    return jsonify({
        "settings": settings_json(settings),
        "status": status_json(settings),
        "suggested_preview": preview_questions(),
        "options": {
            "providers": [{"id": key, **value} for key, value in PROVIDERS.items()],
            "efforts": EFFORT_LEVELS,
            "languages": LANGUAGES,
            "tones": TONES,
            "features": [{"key": k, "label": v[0], "hint": v[1]} for k, v in FEATURE_LABELS.items()],
        },
    })


@bp.put("/assistant")
@permission_required(WRITE)
def update_assistant():
    data = parse_body(AssistantSettingsIn)
    settings = AssistantSettings.get()
    before = _snapshot(settings)

    values = data.model_dump()
    values["features"] = data.features.model_dump()
    values["initial_greeting"] = values["initial_greeting"] or ""
    for field, value in values.items():
        setattr(settings, field, value)
    settings.updated_by_id = current_user.id

    after = _snapshot(settings)
    changed = sorted(k for k in FIELDS if before.get(k) != after.get(k))
    if changed:
        log_activity("update", settings, summary="Updated assistant settings: " + ", ".join(changed)[:200],
                     before={k: before[k] for k in changed}, after={k: after[k] for k in changed})
    db.session.commit()
    return jsonify({"settings": settings_json(settings), "status": status_json(settings),
                    "changed": changed})


@bp.get("/assistant/status")
@permission_required(READ)
def assistant_status():
    return jsonify(status_json(AssistantSettings.get()))


@bp.post("/assistant/avatar")
@permission_required(WRITE)
def upload_avatar():
    try:
        url = save_image(request.files.get("avatar"), "avatars")
    except UploadError as error:
        raise ApiError(str(error), 422, {"avatar": str(error)}) from error
    settings = AssistantSettings.get()
    old = settings.avatar_url
    settings.avatar_url = url
    settings.updated_by_id = current_user.id
    log_activity("update", settings, summary="Changed the assistant's profile image",
                 before={"avatar_url": old}, after={"avatar_url": url})
    db.session.commit()
    delete_upload(old)
    return jsonify({"avatar_url": url}), 201


@bp.delete("/assistant/avatar")
@permission_required(WRITE)
def remove_avatar():
    settings = AssistantSettings.get()
    old = settings.avatar_url
    if old:
        settings.avatar_url = None
        settings.updated_by_id = current_user.id
        log_activity("update", settings, summary="Removed the assistant's profile image",
                     before={"avatar_url": old}, after={"avatar_url": None})
        db.session.commit()
        delete_upload(old)
    return "", 204
