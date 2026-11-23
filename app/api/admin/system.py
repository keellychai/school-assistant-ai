"""System settings API: branding, theme, language and timezone (settings.write),
security rules (settings.security) and the app logo.

Every change is written to the activity log with the old and new values.
"""

from flask import jsonify, request
from flask_login import current_user
from sqlalchemy import select

from ...auth.security import permission_required
from ...extensions import db
from ...models import SystemSetting, User
from ...services import system_settings as ss
from ...services.audit import log_activity
from ...services.uploads import UploadError, delete_upload, save_image
from . import bp
from .schemas import SystemGeneralIn, SystemSecurityIn
from .utils import ApiError, iso, parse_body

READ = "settings.read"
WRITE = "settings.write"
SECURITY = "settings.security"


def _last_change():
    """When the settings page's own keys were last saved, and by whom."""
    keys = list(ss.ALL_FIELDS.values()) + ["app.logo_url"]
    row = db.session.scalar(select(SystemSetting).where(SystemSetting.key.in_(keys))
                            .order_by(SystemSetting.updated_at.desc()).limit(1))
    if row is None:
        return None, None
    user = db.session.get(User, row.updated_by_id) if row.updated_by_id else None
    return iso(row.updated_at), user.full_name if user else None


def _payload():
    updated_at, updated_by = _last_change()
    return {
        "settings": ss.current_values(),
        "updated_at": updated_at,
        "updated_by": updated_by,
        "can_edit": current_user.has_permission(WRITE),
        "can_edit_security": current_user.has_permission(SECURITY),
    }


def _save(fields, values, label):
    """Write changed values; returns the list of changed form fields."""
    before, after = {}, {}
    for field, key in fields.items():
        value = values[field]
        old = ss.get(key)
        if old == value:
            continue
        row = db.session.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key)
            db.session.add(row)
        row.value = value
        row.updated_by_id = current_user.id
        before[field], after[field] = old, value
    if after:
        log_activity("update", entity_type="system_settings", entity_id=label,
                     summary=f"Updated {label} settings: " + ", ".join(sorted(after)).replace("_", " "),
                     before=before, after=after)
    db.session.commit()
    return sorted(after)


@bp.get("/settings")
@permission_required(READ)
def get_settings():
    return jsonify({
        **_payload(),
        "options": {
            "theme_modes": ss.THEME_MODES,
            "languages": ss.LANGUAGES,
            "timezones": ss.timezones(),
            "limits": {field: {"min": ss.SECURITY_LIMITS[key][1], "max": ss.SECURITY_LIMITS[key][2],
                               "default": ss.SECURITY_LIMITS[key][0]}
                       for field, key in ss.SECURITY_FIELDS.items()},
        },
    })


@bp.put("/settings/general")
@permission_required(WRITE)
def update_general():
    data = parse_body(SystemGeneralIn)
    changed = _save(ss.GENERAL_FIELDS, data.model_dump(), "general")
    return jsonify({**_payload(), "changed": changed})


@bp.put("/settings/security")
@permission_required(SECURITY)
def update_security():
    data = parse_body(SystemSecurityIn)
    changed = _save(ss.SECURITY_FIELDS, data.model_dump(), "security")
    return jsonify({**_payload(), "changed": changed})


def _set_logo(url, summary):
    row = db.session.get(SystemSetting, "app.logo_url")
    if row is None:
        row = SystemSetting(key="app.logo_url")
        db.session.add(row)
    old = row.value
    row.value = url
    row.updated_by_id = current_user.id
    log_activity("update", entity_type="system_settings", entity_id="general", summary=summary,
                 before={"logo_url": old}, after={"logo_url": url})
    db.session.commit()
    delete_upload(old)


@bp.post("/settings/logo")
@permission_required(WRITE)
def upload_logo():
    try:
        url = save_image(request.files.get("logo"), "logos")
    except UploadError as error:
        raise ApiError(str(error), 422, {"logo": str(error)}) from error
    _set_logo(url, "Changed the app logo")
    return jsonify({"logo_url": url}), 201


@bp.delete("/settings/logo")
@permission_required(WRITE)
def remove_logo():
    if ss.get("app.logo_url"):
        _set_logo(None, "Removed the app logo")
    return "", 204
