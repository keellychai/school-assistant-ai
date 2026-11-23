"""Option lists and defaults for Assistant Settings (shared by the admin API and,
in Phase 2, the chat engine).

The provider registry only lists what the app can actually call. Model IDs are
the exact Anthropic IDs (no date suffixes). An admin may also enter another
"claude-..." ID so a newly released model can be used without a code change.
"""

import os
import re

DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_KEY_ENV = "ANTHROPIC_API_KEY"

PROVIDERS = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "default_key_env": "ANTHROPIC_API_KEY",
        "models": [
            {"id": "claude-opus-5-5", "label": "Claude Opus 5.5",
             "note": "Recommended default. Strong answers at moderate cost ($4 / $20 per million tokens in/out)."},
            {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5",
             "note": "Faster and cheaper ($2 / $10 per million tokens)."},
            {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5",
             "note": "Fastest and lowest cost ($0.10 / $0.50 per million tokens) for simple questions."},
            {"id": "claude-fable-5-2", "label": "Claude Fable 5.2",
             "note": "Most capable, highest cost ($8 / $40 per million tokens). Rarely needed for a school FAQ."},
        ],
        "model_pattern": r"^claude-[a-z0-9][a-z0-9.\-]{1,60}$",
    },
}

# Effort replaces temperature on current Claude models (sampling parameters are rejected).
EFFORT_LEVELS = [
    {"id": "low", "label": "Low", "note": "Quickest, cheapest. Usually enough for short FAQ-style answers."},
    {"id": "medium", "label": "Medium", "note": "Balanced (the model's default)."},
    {"id": "high", "label": "High", "note": "More thorough reasoning, slower and more tokens."},
    {"id": "xhigh", "label": "Extra high", "note": "For complex, multi-step questions."},
    {"id": "max", "label": "Maximum", "note": "Highest quality regardless of cost and time."},
]
EFFORT_IDS = [e["id"] for e in EFFORT_LEVELS]

LANGUAGES = [{"id": "id", "label": "Bahasa Indonesia"}, {"id": "en", "label": "English"}]
TONES = [
    {"id": "friendly", "label": "Friendly"},
    {"id": "professional", "label": "Professional"},
    {"id": "formal", "label": "Formal"},
    {"id": "casual", "label": "Casual"},
    {"id": "concise", "label": "Concise"},
]

# Chat features an admin can switch on/off. Booleans unless noted.
FEATURE_DEFAULTS = {
    "suggested_questions": True,       # show suggested question buttons when chat opens
    "follow_up_buttons": True,         # show follow-up buttons after an answer
    "show_sources": True,              # show links/sources under answers
    "chat_history": True,              # users can see their previous conversations
    "use_knowledge_base": True,        # answer from knowledge entries when no question matches
    "ai_use_knowledge_context": True,  # give the AI relevant knowledge entries as context
    "ai_history_messages": 10,         # previous messages sent to the AI (int, 0-50)
}

FEATURE_LABELS = {
    "suggested_questions": ("Suggested questions", "Show the suggested question buttons when a user opens AI Chat."),
    "follow_up_buttons": ("Follow-up buttons", "Show configured follow-up questions after an answer."),
    "show_sources": ("Show sources and links", "Show reference links and the knowledge source under answers."),
    "chat_history": ("Chat history", "Let users see and reopen their previous conversations."),
    "use_knowledge_base": ("Answer from the knowledge base", "When no configured question matches, look for a knowledge entry."),
    "ai_use_knowledge_context": ("Give AI the knowledge base", "Send the most relevant knowledge entries to the AI with each question."),
}

# Only names that look like an API-key variable may be used. This stops an admin
# from pointing the AI client at SECRET_KEY, DATABASE_URL or another secret.
KEY_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,60}_API_KEY$")


def features_with_defaults(features):
    merged = dict(FEATURE_DEFAULTS)
    for key, value in (features or {}).items():
        if key in FEATURE_DEFAULTS:
            merged[key] = value
    return merged


def api_key_present(env_name):
    """Whether the server environment has a non-empty value. The value itself is never read out."""
    return bool(env_name and KEY_ENV_PATTERN.match(env_name) and os.environ.get(env_name, "").strip())


def model_label(provider, model_id):
    for model in PROVIDERS.get(provider, {}).get("models", []):
        if model["id"] == model_id:
            return model["label"]
    return model_id
