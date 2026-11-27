"""Phones: list, block a lost or stolen phone (signing it out), and erase it remotely.

Blocking revokes the phone's sessions and refuses its syncs. With `wipe`, the phone erases
its local data (database, keys, sign-in) the next time it reaches the server — even if its
sign-in has expired, through the public status check, which reveals nothing but
"blocked / wipe" for a device id the phone already knows.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.deps import DB, StaffUser
from app.db.models import Device, DeviceEvent, RefreshToken, User
from app.services.audit import audit
from app.services.scope import visible_user_ids_condition

router = APIRouter(tags=["devices"])


class DeviceOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str | None
    username: str | None
    model: str | None
    android_version: str | None
    app_version: str | None
    last_seen_at: datetime | None
    push_enabled: bool
    is_blocked: bool
    blocked_at: datetime | None
    block_reason: str | None
    wipe_requested: bool
    wiped_at: datetime | None
    app_errors_7d: int = 0


def _out(d: Device, u: User | None, errors: int = 0) -> DeviceOut:
    return DeviceOut(
        id=d.id, user_id=d.user_id, user_name=u.full_name if u else None, username=u.username if u else None,
        model=d.model, android_version=d.android_version, app_version=d.app_version, last_seen_at=d.last_seen_at,
        push_enabled=bool(d.push_token), is_blocked=d.is_blocked, blocked_at=d.blocked_at, block_reason=d.block_reason,
        wipe_requested=d.wipe_requested, wiped_at=d.wiped_at, app_errors_7d=errors,
    )  # fmt: skip


def _visible_device(db: DB, actor: User, device_id: uuid.UUID) -> tuple[Device, User | None]:
    row = db.execute(
        select(Device, User)
        .outerjoin(User, User.id == Device.user_id)
        .where(Device.id == device_id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    d, u = row
    if u is None or not db.scalar(
        select(User.id).where(User.id == u.id, visible_user_ids_condition(db, actor))
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    return d, u


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(db: DB, actor: StaffUser) -> list[DeviceOut]:
    rows = (
        db.execute(
            select(Device, User)
            .join(User, User.id == Device.user_id)
            .where(visible_user_ids_condition(db, actor))
            .order_by(Device.last_seen_at.desc().nulls_last())
        )
        .tuples()
        .all()
    )
    since = datetime.now(UTC) - timedelta(days=7)
    errors = {
        dev: int(n)
        for dev, n in db.execute(
            select(DeviceEvent.device_id, func.count())
            .where(
                DeviceEvent.type == "app_error",
                DeviceEvent.occurred_at >= since,
                DeviceEvent.device_id.in_([d.id for d, _ in rows]),
            )
            .group_by(DeviceEvent.device_id)
        )
    }
    return [_out(d, u, errors.get(d.id, 0)) for d, u in rows]


class BlockIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    wipe: bool = Field(False, description="erase the phone's local data at its next contact")


@router.post("/devices/{device_id}/block", response_model=DeviceOut)
def block_device(device_id: uuid.UUID, body: BlockIn, db: DB, actor: StaffUser) -> DeviceOut:
    d, u = _visible_device(db, actor, device_id)
    now = datetime.now(UTC)
    d.is_blocked, d.blocked_at, d.blocked_by, d.block_reason = True, now, actor.id, body.reason
    d.wipe_requested = d.wipe_requested or body.wipe
    d.push_token = None
    tokens = list(
        db.scalars(
            select(RefreshToken).where(
                RefreshToken.device_id == d.id, RefreshToken.revoked_at.is_(None)
            )
        )
    )
    for t in tokens:
        t.revoked_at = now
    revoked = len(tokens)
    audit(
        db, "device_block", actor_id=actor.id, entity="device", entity_id=d.id,
        details={"reason": body.reason, "wipe": body.wipe, "user_id": str(d.user_id), "sessions_revoked": revoked},
    )  # fmt: skip
    db.commit()
    return _out(d, u)


@router.post("/devices/{device_id}/unblock", response_model=DeviceOut)
def unblock_device(device_id: uuid.UUID, db: DB, actor: StaffUser) -> DeviceOut:
    d, u = _visible_device(db, actor, device_id)
    d.is_blocked, d.wipe_requested, d.block_reason = False, False, None
    audit(db, "device_unblock", actor_id=actor.id, entity="device", entity_id=d.id)
    db.commit()
    return _out(d, u)


class StatusIn(BaseModel):
    device_id: uuid.UUID
    wiped: bool = Field(False, description="the phone reports it has erased itself")


class StatusOut(BaseModel):
    blocked: bool
    wipe: bool


@router.post("/devices/status", response_model=StatusOut)
def device_status(body: StatusIn, db: DB) -> StatusOut:
    """No sign-in needed: a blocked phone's sessions are revoked, yet it must learn to wipe."""
    d = db.get(Device, body.device_id)
    if d is None:
        return StatusOut(blocked=False, wipe=False)
    if body.wiped and d.wipe_requested and d.wiped_at is None:
        d.wiped_at = datetime.now(UTC)
        audit(
            db, "device_wiped", entity="device", entity_id=d.id, details={"user_id": str(d.user_id)}
        )
        db.commit()
    return StatusOut(
        blocked=d.is_blocked, wipe=d.is_blocked and d.wipe_requested and d.wiped_at is None
    )
