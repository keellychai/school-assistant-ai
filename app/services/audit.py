"""Write audit-log entries. The caller commits, so the log and the change are saved together."""

from flask import has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models import ActivityLog


def log_activity(action, entity=None, *, entity_type=None, entity_id=None,
                 summary=None, before=None, after=None, actor=None):
    if entity is not None:
        entity_type = entity_type or entity.__tablename__
        entity_id = entity_id if entity_id is not None else getattr(entity, "id", None)

    ip = user_agent = None
    if has_request_context():
        ip = request.remote_addr
        user_agent = (request.user_agent.string or "")[:255]
        if actor is None and current_user and current_user.is_authenticated:
            actor = current_user

    entry = ActivityLog(
        actor_id=getattr(actor, "id", None),
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        summary=(summary or "")[:255] or None,
        before=before,
        after=after,
        ip_address=ip,
        user_agent=user_agent,
    )
    db.session.add(entry)
    return entry
