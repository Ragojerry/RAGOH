"""Live supervision: heartbeats, the supervisor's team view, reassignment, push tokens,
and the per-collector key used for device-to-device relays.

- POST /heartbeat              phone says "I'm alive" (position, battery, unsynced count)
- GET  /team/live              supervisor: every collector's last position and status
- GET  /team/assignments       supervisor: a collector's open work (for reassignment)
- POST /assignments/reassign   supervisor: move open points to another team member
- POST /devices/push-token     phone registers its Expo push token
- GET  /me/relay-key           collector phone fetches the key that seals relay bundles
"""

import base64
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.v1.visit_points import point_out
from app.core.defaults import merged_config
from app.core.deps import DB, CurrentUser, StaffUser
from app.db.models import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
    CollectorDaySummary,
    Device,
    Flag,
    FlagStatus,
    Heartbeat,
    Role,
    Severity,
    Shift,
    Team,
    TeamMember,
    TeamRole,
    User,
    VisitPoint,
)
from app.db.session import get_session_factory
from app.schemas import VisitPointOut
from app.services.audit import audit
from app.services.geo import lonlat, point_wkt
from app.services.push import deliver_pending, notify
from app.services.scope import supervised_team_ids
from app.services.stops import haversine_m
from app.services.tracking import DEFAULT_TZ, SessionFactory, day_bounds, user_campaign

router = APIRouter(tags=["live"])

OPEN_STATUSES = (
    AssignmentStatus.pending,
    AssignmentStatus.en_route,
    AssignmentStatus.arrived,
)
DONE_STATUSES = (
    AssignmentStatus.completed,
    AssignmentStatus.not_found,
    AssignmentStatus.refused,
)


# --- Heartbeat --------------------------------------------------------------------
class HeartbeatIn(BaseModel):
    device_id: uuid.UUID
    recorded_at: datetime
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    battery_pct: int | None = Field(default=None, ge=0, le=100)
    shift_active: bool = False
    pending_count: int = Field(default=0, ge=0)
    app_version: str | None = Field(default=None, max_length=20)


class HeartbeatOut(BaseModel):
    server_time: datetime
    heartbeat_every_s: int


@router.post("/heartbeat", response_model=HeartbeatOut)
def heartbeat(body: HeartbeatIn, db: DB, user: CurrentUser) -> HeartbeatOut:
    now = datetime.now(UTC)
    device = db.get(Device, body.device_id)
    if device is not None and device.is_blocked:
        raise HTTPException(status.HTTP_423_LOCKED, "This device has been blocked.")
    # A phone with a wrong clock must not appear "live" in the future.
    recorded = min(body.recorded_at, now)
    values: dict[str, Any] = {
        "user_id": user.id,
        "device_id": body.device_id,
        "recorded_at": recorded,
        "received_at": now,
        "geom": point_wkt(body.lon, body.lat)
        if body.lat is not None and body.lon is not None
        else None,
        "accuracy_m": body.accuracy_m,
        "battery_pct": body.battery_pct,
        "shift_active": body.shift_active,
        "pending_count": body.pending_count,
        "app_version": body.app_version,
    }
    stmt = pg_insert(Heartbeat).values(**values)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id"],
            set_={k: stmt.excluded[k] for k in values if k != "user_id"},
            # A delayed, older heartbeat must not overwrite a newer one.
            where=Heartbeat.recorded_at <= stmt.excluded.recorded_at,
        )
    )
    if device is not None:
        device.last_seen_at = now
    db.commit()
    c = user_campaign(db, user.id)
    cfg = merged_config(c.config if c else None)
    return HeartbeatOut(server_time=now, heartbeat_every_s=int(cfg["live"]["heartbeat_every_s"]))


# --- Team live view ---------------------------------------------------------------
LiveStatus = Literal["moving", "at_point", "idle", "off_shift", "offline", "never_seen"]


class LiveCollector(BaseModel):
    id: uuid.UUID
    username: str
    full_name: str
    phone: str | None
    team_id: uuid.UUID
    team_name: str
    status: LiveStatus
    last_seen_at: datetime | None = Field(description="Newest heartbeat or GPS fix")
    lat: float | None
    lon: float | None
    accuracy_m: float | None
    battery_pct: int | None
    on_shift: bool
    shift_started_at: datetime | None
    pending_sync: int = Field(description="Records on the phone not yet synced (last heartbeat)")
    at_point_name: str | None = None
    assigned_today: int
    done_today: int
    open_flags: int
    open_high_flags: int
    risk_score: int
    distance_m: float


class TeamLiveOut(BaseModel):
    server_time: datetime
    local_date: date
    teams: list[dict[str, Any]]
    collectors: list[LiveCollector]


def _team_collectors(db: Session, actor: User) -> list[tuple[User, Team]]:
    if actor.role in (Role.admin, Role.manager):
        team_ids = list(
            db.scalars(
                select(Team.id)
                .join(TeamMember, TeamMember.team_id == Team.id)
                .join(User, User.id == TeamMember.user_id)
                .where(User.organisation_id == actor.organisation_id)
                .distinct()
            )
        )
    else:
        team_ids = supervised_team_ids(db, actor)
    if not team_ids:
        return []
    rows = db.execute(
        select(User, Team)
        .join(TeamMember, TeamMember.user_id == User.id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(
            TeamMember.team_id.in_(team_ids),
            TeamMember.role_in_team == TeamRole.collector,
            TeamMember.active_to.is_(None),
        )
        .order_by(Team.name, User.full_name)
    ).tuples()
    seen: set[uuid.UUID] = set()
    out = []
    for u, t in rows:
        if u.id not in seen:  # a collector in two of my teams appears once
            seen.add(u.id)
            out.append((u, t))
    return out


def _recent_fixes(
    db: Session, user_ids: list[uuid.UUID], since: datetime
) -> dict[uuid.UUID, list[tuple[datetime, float, float, float | None, int | None]]]:
    """Synced GPS points since `since`, newest first, per user."""
    rows = db.execute(
        text(
            "SELECT user_id, recorded_at, ST_X(geom), ST_Y(geom), accuracy_m, battery_pct "
            "FROM gps_points WHERE user_id = ANY(:ids) AND recorded_at >= :since "
            "AND recorded_at <= now() + interval '5 minutes' ORDER BY recorded_at DESC"
        ),
        {"ids": user_ids, "since": since},
    )
    out: dict[uuid.UUID, list[tuple[datetime, float, float, float | None, int | None]]] = {}
    for uid, t, x, y, acc, bat in rows:
        out.setdefault(uid, []).append((t, x, y, acc, bat))
    return out


@router.get("/team/live", response_model=TeamLiveOut)
def team_live(db: DB, actor: StaffUser) -> TeamLiveOut:
    now = datetime.now(UTC)
    members = _team_collectors(db, actor)
    c = user_campaign(db, actor.id) or (user_campaign(db, members[0][0].id) if members else None)
    cfg = merged_config(c.config if c else None)
    live = cfg["live"]
    tz = c.timezone if c else DEFAULT_TZ
    today = now.astimezone(_zone(tz)).date()
    start, end = day_bounds(today, tz)
    ids = [u.id for u, _ in members]
    if not ids:
        return TeamLiveOut(server_time=now, local_date=today, teams=[], collectors=[])

    beats = {h.user_id: h for h in db.scalars(select(Heartbeat).where(Heartbeat.user_id.in_(ids)))}
    idle_window = timedelta(minutes=int(live["idle_after_min"]))
    fixes = _recent_fixes(db, ids, max(start, now - timedelta(hours=12)))
    shifts = {
        s.user_id: s
        for s in db.scalars(
            select(Shift)
            .where(Shift.user_id.in_(ids), Shift.started_at >= start, Shift.started_at < end)
            .order_by(Shift.started_at)
        )
    }  # latest shift of the day wins
    counts: dict[uuid.UUID, tuple[int, int]] = {}
    for uid, total, done in db.execute(
        select(
            Assignment.collector_id,
            func.count(),
            func.count().filter(Assignment.status.in_(DONE_STATUSES)),
        )
        .where(
            Assignment.collector_id.in_(ids),
            Assignment.kind == AssignmentKind.visit,
            (Assignment.planned_date == today) | Assignment.planned_date.is_(None),
        )
        .group_by(Assignment.collector_id)
    ):
        counts[uid] = (int(total), int(done))
    flags: dict[uuid.UUID, tuple[int, int]] = {}
    for uid, n, nh in db.execute(
        select(
            Flag.collector_id,
            func.count(),
            func.count().filter(Flag.severity == Severity.high),
        )
        .where(
            Flag.collector_id.in_(ids),
            Flag.status.not_in([FlagStatus.resolved, FlagStatus.dismissed]),
        )
        .group_by(Flag.collector_id)
    ):
        flags[uid] = (int(n), int(nh))
    summaries = {
        s.user_id: s
        for s in db.scalars(
            select(CollectorDaySummary).where(
                CollectorDaySummary.user_id.in_(ids), CollectorDaySummary.local_date == today
            )
        )
    }
    open_points: dict[uuid.UUID, list[tuple[str, float, float]]] = {}
    for uid, name, geom in db.execute(
        select(Assignment.collector_id, VisitPoint.name, VisitPoint.geom)
        .join(VisitPoint, VisitPoint.id == Assignment.visit_point_id)
        .where(Assignment.collector_id.in_(ids), Assignment.status.in_(OPEN_STATUSES))
    ):
        x, y = lonlat(geom)
        open_points.setdefault(uid, []).append((name, x, y))

    out: list[LiveCollector] = []
    for u, team in members:
        hb = beats.get(u.id)
        f = fixes.get(u.id, [])
        lat = lon = acc = None
        bat: int | None = None
        seen: datetime | None = None
        if hb is not None:
            seen = hb.recorded_at
            if hb.geom is not None:
                lon, lat = lonlat(hb.geom)
            acc, bat = hb.accuracy_m, hb.battery_pct
        if f and (seen is None or f[0][0] > seen):
            seen, lon, lat, acc = f[0][0], f[0][1], f[0][2], f[0][3]
            bat = f[0][4] if f[0][4] is not None else bat
        shift = shifts.get(u.id)
        on_shift = bool(shift and shift.ended_at is None) or bool(
            hb and hb.shift_active and hb.recorded_at >= start
        )
        at_point = None
        if lat is not None and lon is not None:
            for name, px, py in open_points.get(u.id, []):
                if haversine_m(lat, lon, py, px) <= float(live["at_point_m"]):
                    at_point = name
                    break
        st: LiveStatus
        if seen is None:
            st = "never_seen"
        elif now - seen > timedelta(minutes=int(live["offline_after_min"])):
            st = "offline"
        elif not on_shift:
            st = "off_shift"
        elif at_point:
            st = "at_point"
        elif _is_idle(f, now, idle_window, float(live["idle_radius_m"])):
            st = "idle"
        else:
            st = "moving"
        total, done = counts.get(u.id, (0, 0))
        nf, nh = flags.get(u.id, (0, 0))
        summ = summaries.get(u.id)
        out.append(
            LiveCollector(
                id=u.id,
                username=u.username,
                full_name=u.full_name,
                phone=u.phone,
                team_id=team.id,
                team_name=team.name,
                status=st,
                last_seen_at=seen,
                lat=lat,
                lon=lon,
                accuracy_m=acc,
                battery_pct=bat,
                on_shift=on_shift,
                shift_started_at=shift.started_at if shift else None,
                pending_sync=hb.pending_count if hb else 0,
                at_point_name=at_point,
                assigned_today=total,
                done_today=done,
                open_flags=nf,
                open_high_flags=nh,
                risk_score=summ.risk_score if summ else 0,
                distance_m=summ.distance_m if summ else 0.0,
            )  # fmt: skip
        )
    teams: dict[uuid.UUID, dict[str, Any]] = {}
    for lc in out:
        t = teams.setdefault(
            lc.team_id,
            {"id": lc.team_id, "name": lc.team_name, "collectors": 0, "on_shift": 0,
             "offline": 0, "assigned": 0, "done": 0, "open_flags": 0},
        )  # fmt: skip
        t["collectors"] += 1
        t["on_shift"] += lc.on_shift
        t["offline"] += lc.status in ("offline", "never_seen")
        t["assigned"] += lc.assigned_today
        t["done"] += lc.done_today
        t["open_flags"] += lc.open_flags
    return TeamLiveOut(
        server_time=now, local_date=today, teams=list(teams.values()), collectors=out
    )


def _zone(tz: str):  # type: ignore[no-untyped-def]
    from zoneinfo import ZoneInfo

    return ZoneInfo(tz)


def _is_idle(
    fixes: list[tuple[datetime, float, float, float | None, int | None]],
    now: datetime,
    window: timedelta,
    radius_m: float,
) -> bool:
    """Idle = synced fixes span the whole window and all lie within radius of the newest."""
    if not fixes or now - fixes[0][0] > window:
        return False
    _, x0, y0, _, _ = fixes[0]
    covered = False
    for t, x, y, _, _ in fixes:
        if haversine_m(y0, x0, y, x) > radius_m:
            return False
        if fixes[0][0] - t >= window:
            covered = True
            break
    return covered


# --- Reassignment -----------------------------------------------------------------
class TeamAssignmentOut(BaseModel):
    id: uuid.UUID
    collector_id: uuid.UUID
    status: AssignmentStatus
    kind: str
    planned_date: date | None
    visit_point: VisitPointOut


def _check_supervises(db: Session, actor: User, collector_id: uuid.UUID) -> None:
    if collector_id not in {u.id for u, _ in _team_collectors(db, actor)}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collector not in your teams")


@router.get("/team/assignments", response_model=list[TeamAssignmentOut])
def team_assignments(
    db: DB,
    actor: StaffUser,
    collector_id: uuid.UUID,
    open_only: bool = Query(default=True),
) -> list[TeamAssignmentOut]:
    _check_supervises(db, actor, collector_id)
    stmt = (
        select(Assignment, VisitPoint)
        .join(VisitPoint, VisitPoint.id == Assignment.visit_point_id)
        .where(Assignment.collector_id == collector_id, VisitPoint.deleted_at.is_(None))
        .order_by(VisitPoint.priority.desc(), VisitPoint.external_id)
    )
    if open_only:
        stmt = stmt.where(Assignment.status.in_(OPEN_STATUSES))
    return [
        TeamAssignmentOut(
            id=a.id,
            collector_id=a.collector_id,
            status=a.status,
            kind=a.kind.value,
            planned_date=a.planned_date,
            visit_point=point_out(p),
        )  # fmt: skip
        for a, p in db.execute(stmt).tuples()
    ]


class ReassignIn(BaseModel):
    assignment_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    to_collector_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=500)


class ReassignOut(BaseModel):
    moved: list[uuid.UUID]
    skipped: list[dict[str, str]]


@router.post("/assignments/reassign", response_model=ReassignOut)
def reassign(
    body: ReassignIn,
    db: DB,
    actor: StaffUser,
    background: BackgroundTasks,
    factory: SessionFactory = Depends(get_session_factory),
) -> ReassignOut:
    """Move open points to another collector of the same team.

    Only points nobody has started are moved; anything with data stays with whoever
    collected it. The old collector's phone drops the point at its next sync, the new
    collector's phone picks it up.
    """
    team_of = {u.id: t.id for u, t in _team_collectors(db, actor)}
    if body.to_collector_id not in team_of:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collector not in your teams")
    target_team = team_of[body.to_collector_id]
    moved: list[uuid.UUID] = []
    skipped: list[dict[str, str]] = []
    now = datetime.now(UTC)
    from_counts: dict[uuid.UUID, int] = {}
    for a in db.scalars(select(Assignment).where(Assignment.id.in_(body.assignment_ids))):
        if a.collector_id not in team_of:
            skipped.append({"id": str(a.id), "reason": "not in your teams"})
        elif team_of[a.collector_id] != target_team:
            skipped.append({"id": str(a.id), "reason": "different team"})
        elif a.status not in OPEN_STATUSES:
            skipped.append({"id": str(a.id), "reason": f"already {a.status.value}"})
        elif a.collector_id == body.to_collector_id:
            skipped.append({"id": str(a.id), "reason": "already assigned to this collector"})
        else:
            from_counts[a.collector_id] = from_counts.get(a.collector_id, 0) + 1
            a.collector_id = body.to_collector_id
            a.status = AssignmentStatus.pending
            a.status_changed_at = now
            a.status_reason = body.reason or "Reassigned"
            a.assigned_by = actor.id
            moved.append(a.id)
    found = {uuid.UUID(s["id"]) for s in skipped} | set(moved)
    skipped.extend(
        {"id": str(i), "reason": "not found"} for i in body.assignment_ids if i not in found
    )
    if moved:
        audit(
            db, "assignments_reassigned", actor_id=actor.id, entity="user",
            entity_id=body.to_collector_id,
            details={"assignment_ids": [str(i) for i in moved], "reason": body.reason,
                     "from": {str(k): v for k, v in from_counts.items()}},
        )  # fmt: skip
        n = len(moved)
        notify(
            db, body.to_collector_id, "reassign", "New points assigned",
            f"{actor.full_name} gave you {n} more point{'s' if n > 1 else ''}. Sync to get them.",
            {"count": n},
        )  # fmt: skip
        for old, k in from_counts.items():
            notify(
                db, old, "reassign", "Points moved",
                f"{k} of your point{'s' if k > 1 else ''} moved to a colleague.", {"count": k},
            )  # fmt: skip
    db.commit()
    background.add_task(deliver_pending, factory)
    return ReassignOut(moved=moved, skipped=skipped)


# --- Push token and relay key -----------------------------------------------------
class PushTokenIn(BaseModel):
    device_id: uuid.UUID
    token: str | None = Field(default=None, max_length=300, description="null to unregister")


@router.post("/devices/push-token", status_code=status.HTTP_204_NO_CONTENT)
def register_push_token(body: PushTokenIn, db: DB, user: CurrentUser) -> None:
    if body.token is not None and not (
        body.token.startswith("ExponentPushToken[") or body.token.startswith("ExpoPushToken[")
    ):
        raise HTTPException(422, "Not an Expo push token")
    device = db.get(Device, body.device_id)
    if device is None:
        device = Device(id=body.device_id, user_id=user.id)
        db.add(device)
    elif device.is_blocked:
        raise HTTPException(status.HTTP_423_LOCKED, "This device has been blocked.")
    if body.token:  # one phone, one token: take it off any other device row
        for other in db.scalars(
            select(Device).where(Device.push_token == body.token, Device.id != body.device_id)
        ):
            other.push_token = None
    device.user_id = user.id  # shared phone: notifications follow whoever signed in
    device.push_token = body.token
    db.commit()


class RelayKeyOut(BaseModel):
    user_id: uuid.UUID
    key: str = Field(description="base64 of 32 bytes; keep in the phone's secure storage")


@router.get("/me/relay-key", response_model=RelayKeyOut)
def relay_key(db: DB, user: CurrentUser) -> RelayKeyOut:
    """Created on first request and stable afterwards (bundles sealed offline stay valid)."""
    if user.relay_key is None:
        user.relay_key = os.urandom(32)
        audit(db, "relay_key_created", actor_id=user.id, entity="user", entity_id=user.id)
        db.commit()
    return RelayKeyOut(user_id=user.id, key=base64.b64encode(user.relay_key).decode())
