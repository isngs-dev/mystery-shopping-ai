from fastapi import Request
from sqlalchemy.orm import Session

from .models import AuditEvent, User


def audit(db: Session, actor: User | None, action: str, *, entity_type: str | None = None,
          entity_id: int | str | None = None, form_id: int | None = None, project_id: int | None = None,
          detail: dict | None = None, request: Request | None = None, actor_label: str | None = None) -> AuditEvent:
    """Append an audit event to the current transaction (caller commits)."""
    ev = AuditEvent(
        actor_id=actor.id if actor else None,
        actor_label=(actor.email if actor else actor_label),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        form_id=form_id,
        project_id=project_id,
        detail=detail,
        ip=request.client.host if request and request.client else None,
    )
    db.add(ev)
    return ev
