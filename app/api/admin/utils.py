"""Helpers shared by every admin API module.

Response contract (the admin JS relies on it):
  list    -> {"items": [...], "page": 1, "per_page": 20, "total": 57, "pages": 3}
  errors  -> {"error": "message", "fields": {"field_name": "message", ...}}   (fields optional)
"""

import math

from flask import jsonify, request
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from ...extensions import db

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


class ApiError(Exception):
    def __init__(self, message, status=400, fields=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.fields = fields or {}

    def to_response(self):
        body = {"error": self.message}
        if self.fields:
            body["fields"] = self.fields
        return jsonify(body), self.status


class FieldErrors(Exception):
    """Raise from a pydantic model_validator to report several fields at once:
    raise FieldErrors({"end": "The end must be after the start."})"""

    def __init__(self, fields):
        super().__init__("; ".join(f"{k}: {v}" for k, v in fields.items()))
        self.fields = fields


def _int_arg(name, default, minimum=1, maximum=None):
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    value = max(value, minimum)
    return min(value, maximum) if maximum else value


def paginate(stmt, serialize, rows=False):
    """Run a SELECT with ?page=&per_page= and return the list contract as a dict.
    rows=True passes whole result rows (for multi-column selects) instead of the first column."""
    page = _int_arg("page", 1)
    per_page = _int_arg("per_page", DEFAULT_PER_PAGE, maximum=MAX_PER_PAGE)
    total = db.session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(math.ceil(total / per_page), 1)
    page = min(page, pages)
    query = stmt.limit(per_page).offset((page - 1) * per_page)
    results = (db.session.execute(query) if rows else db.session.scalars(query)).unique().all()
    return {
        "items": [serialize(row) for row in results],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
    }


def search_term():
    """Trimmed ?q= value, or None. Escapes LIKE wildcards so users can search for % or _."""
    q = (request.args.get("q") or "").strip()[:100]
    if not q:
        return None
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def parse_body(model: type[BaseModel]):
    """Validate the JSON body against a pydantic model; raise ApiError(422) with per-field messages."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("Request body must be a JSON object.", 400)
    try:
        return model.model_validate(payload)
    except FieldErrors as error:
        raise ApiError("Please correct the highlighted fields.", 422, error.fields) from error
    except ValidationError as error:
        fields = {}
        for issue in error.errors():
            name = ".".join(str(part) for part in issue["loc"]) or "body"
            message = issue["msg"].removeprefix("Value error, ")
            fields.setdefault(name, message)
        raise ApiError("Please correct the highlighted fields.", 422, fields) from error


def iso(dt):
    """Stored datetimes are naive UTC; send them as ISO-8601 with an explicit Z."""
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def register_error_handlers(bp):
    @bp.errorhandler(ApiError)
    def _api_error(error):
        db.session.rollback()
        return error.to_response()
