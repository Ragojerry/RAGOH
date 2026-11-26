"""Phone ↔ server sync.

- GET  /sync/pull : snapshot of everything the phone needs to work offline
                    (profile, campaign config, published forms, assignments).
- POST /sync/push : batches of records created offline. Every record carries a client
                    UUID, so retrying a push never creates duplicates.
- POST /submissions/{id}/media : photos, uploaded after the submission itself.

The pull is a full snapshot rather than a delta: a collector has tens to a few hundred
assignments, so this is small and avoids a whole class of "missed deletion" bugs.
"""

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.forms import latest_versions, version_out
from app.api.v1.visit_points import point_out
from app.core.config import get_settings
from app.core.defaults import merged_config
from app.core.deps import DB, CurrentUser
from app.db.models import (
    Assignment,
    Campaign,
    Device,
    FormKind,
    FormVersion,
    Role,
    Shift,
    Submission,
    SubmissionMedia,
    Team,
    TeamMember,
    User,
    VisitPoint,
)
from app.db.models import (
    Form as FormModel,
)
from app.schemas import (
    AssignmentOut,
    CampaignSyncOut,
    GeoPointIn,
    MediaOut,
    RejectedItem,
    SyncPullOut,
    SyncPushIn,
    SyncPushOut,
    UserOut,
)
from app.services.geo import point_wkt

router = APIRouter(tags=["sync"])

ALLOWED_MEDIA = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _geom(p: GeoPointIn | None):  # type: ignore[no-untyped-def]
    return point_wkt(p.lon, p.lat) if p else None


def user_campaign_ids(db: Session, user: User) -> list[uuid.UUID]:
    if user.role in (Role.admin, Role.manager):
        stmt = select(Campaign.id).where(
            Campaign.organisation_id == user.organisation_id, Campaign.is_active.is_(True)
        )
    else:
        stmt = (
            select(Campaign.id)
            .join(Team, Team.campaign_id == Campaign.id)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .where(
                TeamMember.user_id == user.id,
                TeamMember.active_to.is_(None),
                Campaign.is_active.is_(True),
            )
            .distinct()
        )
    return list(db.scalars(stmt))


def _register_device(db: Session, user: User, body: SyncPushIn) -> Device:
    device = db.get(Device, body.device.id)
    if device is None:
        device = Device(id=body.device.id)
        db.add(device)
    if device.is_blocked:
        raise HTTPException(
            status.HTTP_423_LOCKED, "This device has been blocked. Contact your supervisor."
        )
    device.user_id = user.id  # phones may be shared; last user wins
    device.model = body.device.model
    device.android_version = body.device.android_version
    device.app_version = body.device.app_version
    device.last_seen_at = datetime.now(UTC)
    db.flush()
    return device


@router.get("/sync/pull", response_model=SyncPullOut)
def pull(db: DB, user: CurrentUser) -> SyncPullOut:
    campaign_ids = user_campaign_ids(db, user)
    campaigns = (
        list(db.scalars(select(Campaign).where(Campaign.id.in_(campaign_ids))))
        if campaign_ids
        else []
    )
    kinds = None if user.role != Role.collector else {FormKind.survey, FormKind.backcheck}
    forms = [
        version_out(f, v, include_schema=True) for f, v in latest_versions(db, campaign_ids, kinds)
    ]
    rows = db.execute(
        select(Assignment, VisitPoint)
        .join(VisitPoint, VisitPoint.id == Assignment.visit_point_id)
        .where(Assignment.collector_id == user.id, VisitPoint.deleted_at.is_(None))
        .order_by(VisitPoint.priority.desc(), VisitPoint.external_id)
    )
    assignments = [
        AssignmentOut(
            id=a.id,
            collector_id=a.collector_id,
            planned_date=a.planned_date,
            status=a.status,
            visit_point=point_out(p),
        )
        for a, p in rows
    ]
    return SyncPullOut(
        server_time=datetime.now(UTC),
        user=UserOut.model_validate(user),
        campaigns=[
            CampaignSyncOut(id=c.id, name=c.name, config=merged_config(c.config)) for c in campaigns
        ],
        forms=forms,
        assignments=assignments,
    )


@router.post("/sync/push", response_model=SyncPushOut)
def push(body: SyncPushIn, db: DB, user: CurrentUser) -> SyncPushOut:
    settings = get_settings()
    total = len(body.shifts) + len(body.submissions) + len(body.assignment_updates)
    if total > settings.max_push_records:
        raise HTTPException(413, f"Send at most {settings.max_push_records} records per push")

    now = datetime.now(UTC)
    device = _register_device(db, user, body)
    clock_offset = int((body.device_time - now).total_seconds())
    accepted: dict[str, list[uuid.UUID]] = {
        "shifts": [],
        "submissions": [],
        "assignment_updates": [],
    }
    rejected: list[RejectedItem] = []

    # Shifts: insert, or update the end time of a shift we already have.
    for s in body.shifts:
        existing = db.get(Shift, s.id)
        if existing is not None and existing.user_id != user.id:
            rejected.append(RejectedItem(kind="shift", id=s.id, error="belongs to another user"))
            continue
        if existing is None:
            existing = Shift(id=s.id, user_id=user.id, device_id=device.id, started_at=s.started_at)
            existing.start_geom = _geom(s.start_location)
            db.add(existing)
        if s.ended_at is not None:
            existing.ended_at = s.ended_at
            existing.end_reason = s.end_reason or "manual"
            existing.end_geom = _geom(s.end_location)
        accepted["shifts"].append(s.id)

    allowed_campaigns = set(user_campaign_ids(db, user))
    my_assignments = {
        a.id: a for a in db.scalars(select(Assignment).where(Assignment.collector_id == user.id))
    }

    # Submissions: immutable once received; a repeat push is acknowledged without changes.
    for sub in body.submissions:
        existing_sub = db.get(Submission, sub.id)
        if existing_sub is not None:
            if existing_sub.user_id == user.id:
                accepted["submissions"].append(sub.id)
            else:
                rejected.append(RejectedItem(kind="submission", id=sub.id, error="id already used"))
            continue
        fv = db.get(FormVersion, sub.form_version_id)
        form = db.get(FormModel, fv.form_id) if fv else None
        if fv is None or form is None:
            rejected.append(
                RejectedItem(kind="submission", id=sub.id, error="unknown form version")
            )
            continue
        if form.campaign_id not in allowed_campaigns:
            rejected.append(
                RejectedItem(kind="submission", id=sub.id, error="form is not in your campaigns")
            )
            continue
        visit_point_id = sub.visit_point_id
        if sub.assignment_id is not None:
            a = my_assignments.get(sub.assignment_id)
            if a is None:
                rejected.append(
                    RejectedItem(kind="submission", id=sub.id, error="assignment is not yours")
                )
                continue
            visit_point_id = a.visit_point_id
        if sub.ended_at < sub.started_at:
            rejected.append(
                RejectedItem(kind="submission", id=sub.id, error="ended before it started")
            )
            continue
        loc = sub.end_location or sub.start_location
        db.add(
            Submission(
                id=sub.id,
                form_version_id=sub.form_version_id,
                user_id=user.id,
                device_id=device.id,
                shift_id=sub.shift_id,
                assignment_id=sub.assignment_id,
                visit_point_id=visit_point_id,
                started_at=sub.started_at,
                ended_at=sub.ended_at,
                duration_s=int((sub.ended_at - sub.started_at).total_seconds()),
                start_geom=_geom(sub.start_location),
                end_geom=_geom(sub.end_location),
                accuracy_m=loc.accuracy_m if loc else None,
                data=sub.data,
                app_version=body.device.app_version,
                clock_offset_s=clock_offset,
            )
        )
        accepted["submissions"].append(sub.id)

    # Assignment status: last change (by phone time) wins.
    for upd in sorted(body.assignment_updates, key=lambda u: u.changed_at):
        a = my_assignments.get(upd.assignment_id)
        if a is None:
            rejected.append(
                RejectedItem(kind="assignment_update", id=upd.id, error="assignment is not yours")
            )
            continue
        if a.status_changed_at is None or upd.changed_at >= a.status_changed_at:
            a.status = upd.status
            a.status_reason = upd.reason
            a.status_changed_at = upd.changed_at
        accepted["assignment_updates"].append(upd.id)

    db.commit()
    return SyncPushOut(server_time=now, accepted=accepted, rejected=rejected)


@router.post("/submissions/{submission_id}/media", response_model=MediaOut)
async def upload_media(
    submission_id: uuid.UUID,
    db: DB,
    user: CurrentUser,
    file: UploadFile,
    field_name: str = Form(...),
) -> MediaOut:
    settings = get_settings()
    sub = db.get(Submission, submission_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found (push it first)")
    ext = ALLOWED_MEDIA.get(file.content_type or "")
    if ext is None:
        raise HTTPException(415, "Only JPEG, PNG or WebP images are accepted")
    if field_name not in sub.data:
        raise HTTPException(422, f"Submission has no field '{field_name}'")
    content = await file.read(settings.max_media_bytes + 1)
    if len(content) > settings.max_media_bytes:
        raise HTTPException(413, "Image larger than 5 MB")

    key = f"{submission_id}/{field_name}{ext}"
    path = Path(settings.media_dir) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(content)
    os.replace(tmp, path)

    media = db.scalar(
        select(SubmissionMedia).where(
            SubmissionMedia.submission_id == submission_id, SubmissionMedia.field_name == field_name
        )
    )
    if media is None:
        media = SubmissionMedia(submission_id=submission_id, field_name=field_name)
        db.add(media)
    media.filename = file.filename or key
    media.mime = file.content_type or "application/octet-stream"
    media.size_bytes = len(content)
    media.storage_key = key
    db.commit()
    return MediaOut(submission_id=submission_id, field_name=field_name, size_bytes=len(content))
