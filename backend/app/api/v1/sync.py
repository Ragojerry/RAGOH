"""Phone ↔ server sync.

- GET  /sync/pull : snapshot of everything the phone needs to work offline
                    (profile, campaign config, published forms, assignments).
- POST /sync/push : batches of records created offline. Every record carries a client
                    UUID, so retrying a push never creates duplicates.
- POST /submissions/{id}/media : photos, uploaded after the submission itself.

The pull is a full snapshot rather than a delta: a collector has tens to a few hundred
assignments, so this is small and avoids a whole class of "missed deletion" bugs.
"""

import base64
import binascii
import os
import uuid
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.v1.flags import _supervises, messages_for, my_team_ids
from app.api.v1.forms import latest_versions, version_out
from app.api.v1.live import _team_collectors
from app.api.v1.visit_points import point_out
from app.core.config import get_settings
from app.core.defaults import merged_config
from app.core.deps import DB, CurrentUser
from app.db.models import (
    Assignment,
    Backcheck,
    BackcheckStatus,
    Campaign,
    Device,
    DeviceEvent,
    Flag,
    FlagAction,
    FlagStatus,
    FormKind,
    FormVersion,
    GpsPoint,
    Message,
    Role,
    Shift,
    Submission,
    SubmissionMedia,
    Team,
    TeamMember,
    TeamRole,
    User,
    VisitPoint,
)
from app.db.models import (
    Form as FormModel,
)
from app.db.session import get_session_factory
from app.schemas import (
    AssignmentOut,
    CampaignSyncOut,
    GeoPointIn,
    MediaOut,
    RejectedItem,
    RelayIn,
    SyncContactOut,
    SyncFlagOut,
    SyncMessageOut,
    SyncPullOut,
    SyncPushIn,
    SyncPushOut,
    TeamMemberSyncOut,
    UserOut,
)
from app.services.audit import audit
from app.services.geo import point_wkt
from app.services.push import deliver_pending
from app.services.scope import visible_user_ids_condition
from app.services.tracking import (
    DEFAULT_TZ,
    SessionFactory,
    ensure_partitions,
    local_dates,
    run_processing,
    user_campaign,
)

router = APIRouter(tags=["sync"])

MAX_RELAY_PLAIN = 8 * 1024 * 1024  # decompressed bundle limit (zip-bomb guard)
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


def _register_device(db: Session, user: User, body: SyncPushIn, seen: bool = True) -> Device:
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
    if seen:
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
            kind=a.kind.value,
            note=a.note,
        )
        for a, p in rows
    ]
    msgs = messages_for(db, user, since=datetime.now(UTC) - timedelta(days=14))
    senders: dict[uuid.UUID, str] = {}
    if msgs:
        ids = {m.sender_id for m in msgs}
        senders = {
            uid: name
            for uid, name in db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
        }
    my_flags = db.scalars(
        select(Flag).where(
            Flag.collector_id == user.id,
            Flag.collector_visible.is_(True),
            Flag.status.not_in([FlagStatus.resolved, FlagStatus.dismissed]),
        )
    )
    supervisors = db.scalars(
        select(User)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(
            TeamMember.role_in_team == TeamRole.supervisor,
            TeamMember.active_to.is_(None),
            TeamMember.team_id.in_(my_team_ids(db, user)),
        )
        .distinct()
    )
    return SyncPullOut(
        server_time=datetime.now(UTC),
        user=UserOut.model_validate(user),
        campaigns=[
            CampaignSyncOut(id=c.id, name=c.name, config=merged_config(c.config)) for c in campaigns
        ],
        forms=forms,
        assignments=assignments,
        messages=[
            SyncMessageOut(
                id=m.id,
                sender_id=m.sender_id,
                sender_name=senders.get(m.sender_id, ""),
                recipient_id=m.recipient_id,
                flag_id=m.flag_id,
                body=m.body,
                sent_at=m.sent_at,
            )
            for m in msgs
        ],
        my_flags=[
            SyncFlagOut(
                id=f.id,
                title=f.title,
                local_date=f.local_date,
                status=f.status.value,
                details=f.details,
            )
            for f in my_flags
        ],
        supervisors=[
            SyncContactOut(id=u.id, full_name=u.full_name, phone=u.phone)
            for u in supervisors
            if u.id != user.id
        ],
        team_members=[
            TeamMemberSyncOut(
                id=u.id,
                username=u.username,
                full_name=u.full_name,
                phone=u.phone,
                team_id=t.id,
                team_name=t.name,
            )  # fmt: skip
            for u, t in (_team_collectors(db, user) if user.role != Role.collector else [])
        ],
    )


RECORD_KINDS = (
    "shifts", "submissions", "assignment_updates", "gps_points", "device_events", "messages",
    "flag_replies",
)  # fmt: skip


def _count(body: SyncPushIn) -> int:
    return sum(len(getattr(body, k)) for k in RECORD_KINDS)


def apply_records(
    db: Session,
    user: User,
    device: Device,
    body: SyncPushIn,
    reference_time: datetime | None = None,
) -> tuple[dict[str, list[uuid.UUID]], list[RejectedItem], list[datetime]]:
    """Store one batch of a user's offline records. Idempotent: replays are acknowledged.

    Used for a phone's own push and for data relayed through a supervisor's phone.
    `reference_time` is the true time at which `body.device_time` was read (for a relay,
    the moment the supervisor scanned it), so the phone's clock error can be measured.
    """
    now = reference_time or datetime.now(UTC)
    clock_offset = int((body.device_time - now).total_seconds())
    accepted: dict[str, list[uuid.UUID]] = {k: [] for k in RECORD_KINDS}
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

    # GPS points: bulk insert, duplicates (retries) silently ignored.
    touched: list[datetime] = []
    if body.gps_points:
        ensure_partitions(db, (p.recorded_at for p in body.gps_points))
        rows = [
            {
                "id": p.id,
                "recorded_at": p.recorded_at,
                "user_id": user.id,
                "shift_id": p.shift_id,
                "device_id": device.id,
                "geom": point_wkt(p.lon, p.lat),
                "accuracy_m": p.accuracy_m,
                "altitude_m": p.altitude_m,
                "speed_mps": p.speed_mps,
                "bearing": p.bearing,
                "battery_pct": p.battery_pct,
                "is_mock": p.is_mock,
                "provider": p.provider,
                "network_type": p.network_type,
            }  # fmt: skip
            for p in body.gps_points
        ]
        for i in range(0, len(rows), 500):
            db.execute(
                pg_insert(GpsPoint)
                .values(rows[i : i + 500])
                .on_conflict_do_nothing(index_elements=["id", "recorded_at"])
            )
        accepted["gps_points"] = [p.id for p in body.gps_points]
        touched.extend(p.recorded_at for p in body.gps_points)

    for ev in body.device_events:
        if db.get(DeviceEvent, ev.id) is None:
            db.add(
                DeviceEvent(
                    id=ev.id,
                    user_id=user.id,
                    shift_id=ev.shift_id,
                    device_id=device.id,
                    type=ev.type,
                    occurred_at=ev.occurred_at,
                    details=ev.details,
                )  # fmt: skip
            )
        accepted["device_events"].append(ev.id)

    # Messages written on the phone (to a supervisor of the user's team).
    for pm in body.messages:
        if db.get(Message, pm.id) is not None:
            accepted["messages"].append(pm.id)
            continue
        if user.role == Role.collector and not _supervises(db, pm.recipient_id, user.id):
            rejected.append(
                RejectedItem(kind="message", id=pm.id, error="recipient is not your supervisor")
            )
            continue
        if user.role != Role.collector and not db.scalar(
            select(User.id).where(User.id == pm.recipient_id, visible_user_ids_condition(db, user))
        ):
            rejected.append(RejectedItem(kind="message", id=pm.id, error="recipient not found"))
            continue
        db.add(
            Message(
                id=pm.id,
                sender_id=user.id,
                recipient_id=pm.recipient_id,
                body=pm.body,
                sent_at=pm.sent_at,
            )
        )
        accepted["messages"].append(pm.id)

    # Explanations for flags the supervisor asked about.
    for fr in body.flag_replies:
        if db.get(FlagAction, fr.id) is not None:
            accepted["flag_replies"].append(fr.id)
            continue
        flag = db.get(Flag, fr.flag_id)
        if flag is None or flag.collector_id != user.id or not flag.collector_visible:
            rejected.append(RejectedItem(kind="flag_reply", id=fr.id, error="flag not found"))
            continue
        db.add(
            FlagAction(
                id=fr.id,
                flag_id=flag.id,
                actor_id=user.id,
                action="reply",
                comment=fr.comment,
                created_at=fr.sent_at,
            )
        )
        asker = db.scalar(
            select(FlagAction.actor_id)
            .where(FlagAction.flag_id == flag.id, FlagAction.action == "request_explanation")
            .order_by(FlagAction.created_at.desc())
        )
        if asker:
            db.add(
                Message(
                    sender_id=user.id,
                    recipient_id=asker,
                    flag_id=flag.id,
                    body=fr.comment,
                    sent_at=fr.sent_at,
                )
            )
        accepted["flag_replies"].append(fr.id)

    accepted_subs = [s.id for s in body.submissions if s.id in accepted["submissions"]]
    complete_backchecks(db, accepted_subs)
    touched.extend(s.ended_at for s in body.submissions if s.id in accepted["submissions"])
    return accepted, rejected, touched


def _supervisor_may_relay(db: Session, actor: User, collector: User) -> bool:
    if collector.organisation_id != actor.organisation_id:
        return False
    if actor.role in (Role.admin, Role.manager):
        return True
    return actor.role == Role.supervisor and _supervises(db, actor.id, collector.id)


def open_relay(key: bytes, relay: RelayIn) -> SyncPushIn:
    """Decrypt and parse a sealed bundle. Raises ValueError if it is not genuine."""
    try:
        nonce = base64.b64decode(relay.nonce, validate=True)
        ct = base64.b64decode(relay.ciphertext, validate=True)
        plain = ChaCha20Poly1305(key).decrypt(nonce, ct, relay.collector_id.bytes)
        d = zlib.decompressobj()
        raw = d.decompress(plain, MAX_RELAY_PLAIN)
        if d.unconsumed_tail or not d.eof:
            raise ValueError("bundle too large or truncated")
        return SyncPushIn.model_validate_json(raw)
    except (InvalidTag, binascii.Error, zlib.error) as e:
        raise ValueError("bundle could not be opened (wrong key or damaged)") from e
    except ValidationError as e:
        raise ValueError(f"bundle content is invalid: {e.error_count()} errors") from e


@router.post("/sync/push", response_model=SyncPushOut)
def push(
    body: SyncPushIn,
    db: DB,
    user: CurrentUser,
    background: BackgroundTasks,
    session_factory: SessionFactory = Depends(get_session_factory),
) -> SyncPushOut:
    settings = get_settings()
    if _count(body) > settings.max_push_records:
        raise HTTPException(413, f"Send at most {settings.max_push_records} records per push")

    now = datetime.now(UTC)
    device = _register_device(db, user, body)
    accepted, rejected, touched = apply_records(db, user, device, body)
    work: set[tuple[uuid.UUID, Any]] = set()
    tz_of: dict[uuid.UUID, str] = {}

    def add_work(uid: uuid.UUID, stamps: list[datetime]) -> None:
        if not stamps:
            return
        if uid not in tz_of:
            c = user_campaign(db, uid)
            tz_of[uid] = c.timezone if c else DEFAULT_TZ
        work.update((uid, d) for d in local_dates(stamps, tz_of[uid]))

    add_work(user.id, touched)

    # Data carried from collectors' phones that had no network (device-to-device).
    accepted["relays"] = []
    for relay in body.relays:
        collector = db.get(User, relay.collector_id)
        if collector is None or not _supervisor_may_relay(db, user, collector):
            rejected.append(RejectedItem(kind="relay", id=relay.id, error="not your collector"))
            continue
        if collector.relay_key is None:
            rejected.append(RejectedItem(kind="relay", id=relay.id, error="collector has no key"))
            continue
        try:
            inner = open_relay(collector.relay_key, relay)
        except ValueError as e:
            rejected.append(RejectedItem(kind="relay", id=relay.id, error=str(e)))
            continue
        if _count(inner) > settings.max_push_records:
            rejected.append(RejectedItem(kind="relay", id=relay.id, error="bundle too large"))
            continue
        try:
            with db.begin_nested():
                c_device = _register_device(db, collector, inner, seen=False)
                scanned = relay.scanned_at - (body.device_time - now)  # supervisor clock fix
                r_acc, r_rej, r_touched = apply_records(db, collector, c_device, inner, scanned)
        except HTTPException as e:
            rejected.append(RejectedItem(kind="relay", id=relay.id, error=str(e.detail)))
            continue
        add_work(collector.id, r_touched)
        accepted["relays"].append(relay.id)
        audit(
            db, "relay_received", actor_id=user.id, entity="user", entity_id=collector.id,
            details={
                "relay_id": str(relay.id),
                "accepted": {k: len(v) for k, v in r_acc.items() if v},
                "rejected": len(r_rej),
            },
        )  # fmt: skip

    db.commit()
    if work:
        background.add_task(run_processing, session_factory, work)
    else:
        background.add_task(deliver_pending, session_factory)
    return SyncPushOut(server_time=now, accepted=accepted, rejected=rejected)


def complete_backchecks(db: Session, submission_ids: list[uuid.UUID]) -> None:
    """A back-check submission closes its back-check and records how it compares."""
    for sid in submission_ids:
        sub = db.get(Submission, sid)
        if sub is None or sub.assignment_id is None:
            continue
        bc = db.scalar(
            select(Backcheck).where(
                Backcheck.assignment_id == sub.assignment_id,
                Backcheck.status == BackcheckStatus.assigned,
            )
        )
        if bc is None:
            continue
        original = (
            db.get(Submission, bc.original_submission_id) if bc.original_submission_id else None
        )
        bc.match = compare_answers(original.data if original else {}, sub.data)
        bc.status, bc.backcheck_submission_id, bc.completed_at = (
            BackcheckStatus.completed,
            sub.id,
            datetime.now(UTC),
        )
        if bc.flag_id:
            db.add(
                FlagAction(
                    flag_id=bc.flag_id,
                    actor_id=sub.user_id,
                    action="backcheck_completed",
                    data={"backcheck_id": str(bc.id), **bc.match},
                )
            )


def compare_answers(original: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Compare answers to questions asked in both interviews."""
    common = [
        k
        for k, v in check.items()
        if k in original
        and isinstance(v, str | int | float | bool)
        and not isinstance(original[k], dict | list)
    ]
    differ = [
        {"question": k, "original": original[k], "backcheck": check[k]}
        for k in common
        if str(original[k]) != str(check[k])
    ]
    if not original:
        return {
            "compared": 0,
            "matching": 0,
            "match_share": None,
            "differences": [],
            "note": "no original survey found",
        }
    share = round((len(common) - len(differ)) / len(common), 2) if common else None
    return {
        "compared": len(common),
        "matching": len(common) - len(differ),
        "match_share": share,
        "differences": differ,
    }


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
