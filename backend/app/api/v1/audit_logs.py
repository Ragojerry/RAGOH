"""Audit log viewer: who signed in, viewed tracks, exported data, blocked phones, changed
settings. Admins see everything; managers see entries by or about people in their area."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import cast, func, or_, select
from sqlalchemy.types import String

from app.core.deps import DB, ManagerOrAdmin
from app.db.models import AuditLog, Role, User
from app.services.scope import visible_user_ids_condition

router = APIRouter(tags=["audit"])

# Actions that touch personal tracking data, for the "privacy" filter.
PRIVACY_ACTIONS = [
    "track_view",
    "export",
    "device_block",
    "device_wiped",
    "relay_received",
    "consent",
]


class AuditOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    action: str
    actor_id: uuid.UUID | None
    actor_name: str | None
    entity: str | None
    entity_id: str | None
    entity_name: str | None
    details: dict[str, Any]
    ip: str | None


class AuditPage(BaseModel):
    total: int
    items: list[AuditOut]
    actions: list[str]


@router.get("/audit-logs", response_model=AuditPage)
def list_audit_logs(
    db: DB,
    actor: ManagerOrAdmin,
    action: str | None = None,
    privacy_only: bool = False,
    user_id: uuid.UUID | None = Query(None, description="entries by or about this user"),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AuditPage:
    stmt = select(AuditLog)
    if actor.role != Role.admin:
        visible = select(User.id).where(visible_user_ids_condition(db, actor))
        stmt = stmt.where(
            or_(
                AuditLog.actor_id.in_(visible),
                AuditLog.entity_id.in_(
                    select(cast(User.id, String)).where(visible_user_ids_condition(db, actor))
                ),
            )
        )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if privacy_only:
        stmt = stmt.where(AuditLog.action.in_(PRIVACY_ACTIONS))
    if user_id:
        stmt = stmt.where(or_(AuditLog.actor_id == user_id, AuditLog.entity_id == str(user_id)))
    if date_from:
        stmt = stmt.where(
            AuditLog.created_at
            >= datetime.combine(date_from, time.min, tzinfo=UTC) - timedelta(hours=1)
        )
    if date_to:
        stmt = stmt.where(
            AuditLog.created_at
            < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC)
            - timedelta(hours=1)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)))
    ids: set[uuid.UUID] = {r.actor_id for r in rows if r.actor_id}
    for r in rows:
        if r.entity == "user" and r.entity_id and _is_uuid(r.entity_id):
            ids.add(uuid.UUID(r.entity_id))
    names = (
        {u: n for u, n in db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))}
        if ids
        else {}
    )
    actions = list(db.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action)))
    return AuditPage(
        total=total,
        actions=actions,
        items=[
            AuditOut(
                id=r.id,
                created_at=r.created_at,
                action=r.action,
                actor_id=r.actor_id,
                actor_name=names.get(r.actor_id) if r.actor_id else None,
                entity=r.entity,
                entity_id=r.entity_id,
                entity_name=names.get(uuid.UUID(r.entity_id))
                if r.entity == "user" and r.entity_id and _is_uuid(r.entity_id)
                else None,
                details=r.details,
                ip=r.ip,
            )  # fmt: skip
            for r in rows
        ],
    )


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False
