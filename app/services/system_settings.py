"""General system settings: the keys, their defaults and typed getters.

Values live in the `system_settings` key/value table. Reading always falls back
to the default here, so a missing or damaged row never breaks sign-in or pages.
"""

import re
from zoneinfo import available_timezones

from ..models import SystemSetting

DEFAULT_TIMEZONE = "Asia/Jakarta"
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

THEME_MODES = [
    {"id": "light", "label": "Light"},
    {"id": "dark", "label": "Dark"},
    {"id": "system", "label": "Follow each device"},
]
LANGUAGES = [
    {"id": "id", "label": "Bahasa Indonesia", "locale": "id-ID"},
    {"id": "en", "label": "English", "locale": "en-GB"},
]

# Numeric security settings: (default, minimum, maximum).
SECURITY_LIMITS = {
    "security.session_timeout_minutes": (480, 5, 10080),   # 5 minutes .. 7 days
    "security.max_failed_logins": (5, 3, 20),
    "security.lockout_minutes": (15, 1, 1440),
    "security.password_min_length": (8, 8, 64),
}

DEFAULTS = {
    "app.name": "SchoolSync AI",
    "app.tagline": "AI-Based School Administration and Communication Assistant",
    "app.logo_url": None,
    "theme.mode": "light",
    "theme.primary_color": "#4f63df",
    "locale.default_language": "id",
    "locale.timezone": DEFAULT_TIMEZONE,
    **{key: limits[0] for key, limits in SECURITY_LIMITS.items()},
}

# Form field name -> setting key (what the settings page edits; the logo has its own endpoint).
GENERAL_FIELDS = {
    "app_name": "app.name",
    "app_tagline": "app.tagline",
    "theme_mode": "theme.mode",
    "primary_color": "theme.primary_color",
    "default_language": "locale.default_language",
    "timezone": "locale.timezone",
}
SECURITY_FIELDS = {
    "session_timeout_minutes": "security.session_timeout_minutes",
    "max_failed_logins": "security.max_failed_logins",
    "lockout_minutes": "security.lockout_minutes",
    "password_min_length": "security.password_min_length",
}
ALL_FIELDS = {**GENERAL_FIELDS, **SECURITY_FIELDS}

_TIMEZONES = None


def timezones():
    """Sorted IANA timezone names (cached; the list doesn't change while running)."""
    global _TIMEZONES
    if _TIMEZONES is None:
        _TIMEZONES = sorted(tz for tz in available_timezones() if "/" in tz and not tz.startswith(("Etc/", "SystemV/")))
        if "UTC" not in _TIMEZONES:
            _TIMEZONES.insert(0, "UTC")
    return _TIMEZONES


def get(key):
    return SystemSetting.get_value(key, DEFAULTS.get(key))


def get_int(key):
    """A security number, clamped to its allowed range (falls back to the default)."""
    default, low, high = SECURITY_LIMITS[key]
    try:
        value = int(get(key))
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


def session_timeout_minutes():
    return get_int("security.session_timeout_minutes")


def password_min_length():
    return get_int("security.password_min_length")


def lockout_policy():
    """(max failed attempts, lockout minutes)."""
    return get_int("security.max_failed_logins"), get_int("security.lockout_minutes")


def safe_color(value, default=DEFAULTS["theme.primary_color"]):
    """The colour is written into a style attribute, so only #rrggbb is accepted."""
    return value if isinstance(value, str) and HEX_COLOR.match(value) else default


def locale_tag():
    language = get("locale.default_language")
    return next((l["locale"] for l in LANGUAGES if l["id"] == language), "id-ID")


def branding():
    mode = get("theme.mode")
    return {
        "name": get("app.name") or DEFAULTS["app.name"],
        "tagline": get("app.tagline") or "",
        "logo_url": get("app.logo_url"),
        "primary_color": safe_color(get("theme.primary_color")),
        "theme": mode if mode in {m["id"] for m in THEME_MODES} else "light",
        "language": get("locale.default_language") or "id",
        "locale": locale_tag(),
    }


def current_values():
    """Values for the settings form, keyed by form field name."""
    values = {field: get(key) for field, key in ALL_FIELDS.items()}
    values.update({field: get_int(key) for field, key in SECURITY_FIELDS.items()})
    values["primary_color"] = safe_color(values["primary_color"])
    values["logo_url"] = get("app.logo_url")
    return values
