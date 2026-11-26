import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def audit(
    db: Session,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    entity: str | None = None,
    entity_id: Any = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Add an audit record to the current transaction (committed with the caller's work)."""
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=details or {},
            ip=ip,
        )
    )
