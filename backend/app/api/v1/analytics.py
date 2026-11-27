"""Dashboard data: boundaries, points by status, team analytics, coverage.

Everything is scoped: supervisors see their teams, managers their geography, admins
their organisation. Geometry is simplified server-side so pages load on slow links.
"""

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Float, and_, case, cast, func, literal_column, select
from sqlalchemy.orm import Session

from app.core.deps import DB, StaffUser
from app.db.models import (
    AdminArea,
    AreaLevel,
    Assignment,
    AssignmentKind,
    AssignmentStatus,
    CollectorDaySummary,
    Flag,
    FlagStatus,
    Form,
    FormKind,
    FormVersion,
    Role,
    Stop,
    Submission,
    Team,
    TeamMember,
    TeamRole,
    User,
    VisitPoint,
)
from app.services.scope import area_filter, visible_team_ids

router = APIRouter(tags=["dashboard"])

DONE = (AssignmentStatus.completed, AssignmentStatus.not_found, AssignmentStatus.refused)
MAX_RANGE_DAYS = 92


def _range(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    if date_to < date_from:
        raise HTTPException(422, "date_to is before date_from")
    if (date_to - date_from).days > MAX_RANGE_DAYS:
        raise HTTPException(422, f"Pick at most {MAX_RANGE_DAYS} days")
    # Day boundaries in UTC are close enough for range filters (Nigeria is UTC+1);
    # per-day figures come from summaries computed in the campaign's time zone.
    start = datetime.combine(date_from, time.min, tzinfo=UTC) - timedelta(hours=1)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC) - timedelta(hours=1)
    return start, end


def team_collectors(
    db: Session, actor: User, team_id: uuid.UUID | None = None
) -> dict[uuid.UUID, tuple[User, Team]]:
    """Collectors (current members) of the teams the actor may see."""
    teams = visible_team_ids(db, actor)
    if team_id is not None:
        if team_id not in teams:
            raise HTTPException(404, "Team not found")
        teams = [team_id]
    if not teams:
        return {}
    out: dict[uuid.UUID, tuple[User, Team]] = {}
    for u, t in db.execute(
        select(User, Team)
        .join(TeamMember, TeamMember.user_id == User.id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(
            TeamMember.team_id.in_(teams),
            TeamMember.role_in_team == TeamRole.collector,
            TeamMember.active_to.is_(None),
        )
        .order_by(Team.name, User.full_name)
    ).tuples():
        out.setdefault(u.id, (u, t))
    return out


# --- Map layers -----------------------------------------------------------------
@router.get("/map/areas.geojson")
def areas_geojson(
    db: DB,
    actor: StaffUser,
    level: AreaLevel = AreaLevel.ward,
    tolerance: float = Query(0.0003, ge=0, le=0.01, description="simplification, degrees"),
) -> dict[str, Any]:
    """Area boundaries at one level, simplified (~30 m) for the map."""
    rows = db.execute(
        select(
            AdminArea.id,
            AdminArea.name,
            AdminArea.code,
            AdminArea.parent_id,
            func.ST_AsGeoJSON(func.ST_SimplifyPreserveTopology(AdminArea.geom, tolerance), 6),
        ).where(
            AdminArea.level == level,
            AdminArea.geom.is_not(None),
            area_filter(db, actor, AdminArea.path),
        )
    )
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": str(i),
                "properties": {
                    "id": str(i),
                    "name": n,
                    "code": c,
                    "parent_id": str(p) if p else None,
                },
                "geometry": json.loads(g),
            }
            for i, n, c, p, g in rows
            if g
        ],
    }


@router.get("/map/points.geojson")
def points_geojson(
    db: DB,
    actor: StaffUser,
    campaign_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    limit: int = Query(20000, ge=1, le=50000),
) -> dict[str, Any]:
    """Visit points with their assignment status and collector, for colouring the map."""
    collectors = team_collectors(db, actor, team_id)
    stmt = (
        select(
            VisitPoint.id,
            VisitPoint.name,
            VisitPoint.external_id,
            func.ST_X(VisitPoint.geom),
            func.ST_Y(VisitPoint.geom),
            Assignment.status,
            Assignment.collector_id,
            Assignment.kind,
        )  # fmt: skip
        .join(AdminArea, AdminArea.id == VisitPoint.admin_area_id)
        .outerjoin(
            Assignment,
            and_(
                Assignment.visit_point_id == VisitPoint.id, Assignment.kind == AssignmentKind.visit
            ),
        )
        .where(VisitPoint.deleted_at.is_(None), area_filter(db, actor, AdminArea.path))
    )
    if campaign_id:
        stmt = stmt.where(VisitPoint.campaign_id == campaign_id)
    if team_id or actor.role == Role.supervisor:
        stmt = stmt.where(Assignment.collector_id.in_(list(collectors)))
    features = []
    for pid, name, ext, x, y, st, cid, _ in db.execute(stmt.limit(limit)):
        who = collectors.get(cid) if cid else None
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(x, 6), round(y, 6)]},
                "properties": {
                    "id": str(pid),
                    "name": name,
                    "external_id": ext,
                    "status": st.value if st else "unassigned",
                    "done": st in DONE if st else False,
                    "collector": who[0].full_name if who else None,
                },
            }  # fmt: skip
        )
    return {"type": "FeatureCollection", "features": features}


# --- Team overview --------------------------------------------------------------
class CollectorStats(BaseModel):
    id: uuid.UUID
    full_name: str
    username: str
    team_id: uuid.UUID
    team_name: str
    assigned: int
    done: int
    coverage_pct: float
    days_worked: int
    submissions: int
    median_interview_s: int | None
    avg_dwell_at_points_s: int | None
    distance_km: float
    flags: int
    high_flags: int
    flags_per_100: float | None = Field(description="flags per 100 submissions")
    avg_risk: float
    max_risk: int
    spot_checks: int = Field(description="supervision checklists about this collector")
    last_spot_check: datetime | None


class DailyRow(BaseModel):
    date: date
    active_collectors: int
    submissions: int
    distance_km: float
    flags: int
    avg_risk: float


class TeamOverviewOut(BaseModel):
    date_from: date
    date_to: date
    collectors: list[CollectorStats]
    daily: list[DailyRow]


@router.get("/analytics/team", response_model=TeamOverviewOut)
def team_overview(
    db: DB,
    actor: StaffUser,
    date_from: date,
    date_to: date,
    team_id: uuid.UUID | None = None,
) -> TeamOverviewOut:
    start, end = _range(date_from, date_to)
    members = team_collectors(db, actor, team_id)
    ids = list(members)
    if not ids:
        return TeamOverviewOut(date_from=date_from, date_to=date_to, collectors=[], daily=[])

    assigned = {
        uid: (int(total), int(done))
        for uid, total, done in db.execute(
            select(
                Assignment.collector_id,
                func.count(),
                func.count().filter(Assignment.status.in_(DONE)),
            )
            .where(Assignment.collector_id.in_(ids), Assignment.kind == AssignmentKind.visit)
            .group_by(Assignment.collector_id)
        )
    }
    survey = (
        select(Submission.user_id, Submission.duration_s, Submission.ended_at)
        .join(FormVersion, FormVersion.id == Submission.form_version_id)
        .join(Form, Form.id == FormVersion.form_id)
        .where(
            Form.kind == FormKind.survey,
            Submission.user_id.in_(ids),
            Submission.ended_at >= start,
            Submission.ended_at < end,
        )
        .subquery()
    )
    subs = {
        uid: (int(n), int(med) if med is not None else None)
        for uid, n, med in db.execute(
            select(
                survey.c.user_id,
                func.count(),
                func.percentile_cont(0.5).within_group(survey.c.duration_s),
            ).group_by(survey.c.user_id)
        )
    }
    dwell = {
        uid: int(avg)
        for uid, avg in db.execute(
            select(Stop.user_id, func.avg(Stop.dwell_s))
            .where(
                Stop.user_id.in_(ids),
                Stop.local_date >= date_from,
                Stop.local_date <= date_to,
                Stop.classification == "at_assigned_point",
            )
            .group_by(Stop.user_id)
        )
        if avg is not None
    }
    sums = {
        uid: (int(days), float(dist or 0), float(avg_r or 0), int(max_r or 0))
        for uid, days, dist, avg_r, max_r in db.execute(
            select(
                CollectorDaySummary.user_id,
                func.count().filter(
                    (CollectorDaySummary.n_submissions > 0) | (CollectorDaySummary.n_gps_points > 0)
                ),
                func.sum(CollectorDaySummary.distance_m),
                func.avg(CollectorDaySummary.risk_score),
                func.max(CollectorDaySummary.risk_score),
            )
            .where(
                CollectorDaySummary.user_id.in_(ids),
                CollectorDaySummary.local_date >= date_from,
                CollectorDaySummary.local_date <= date_to,
            )
            .group_by(CollectorDaySummary.user_id)
        )
    }
    flags = {
        uid: (int(n), int(nh))
        for uid, n, nh in db.execute(
            select(
                Flag.collector_id,
                func.count(),
                func.count().filter(Flag.severity == "high"),
            )
            .where(
                Flag.collector_id.in_(ids),
                Flag.local_date >= date_from,
                Flag.local_date <= date_to,
                Flag.status != FlagStatus.dismissed,
            )
            .group_by(Flag.collector_id)
        )
    }
    usernames = {u.username: u.id for u, _ in members.values()}
    checks: dict[uuid.UUID, tuple[int, datetime]] = {}
    for uname, n, last in db.execute(
        select(
            Submission.data["collector_username"].astext,
            func.count(),
            func.max(Submission.ended_at),
        )
        .join(FormVersion, FormVersion.id == Submission.form_version_id)
        .join(Form, Form.id == FormVersion.form_id)
        .where(
            Form.kind == FormKind.supervision,
            Submission.data["collector_username"].astext.in_(list(usernames)),
            Submission.ended_at >= start,
            Submission.ended_at < end,
        )
        .group_by(literal_column("1"))  # same JSON lookup as the first column
    ):
        checks[usernames[uname]] = (int(n), last)

    rows: list[CollectorStats] = []
    for uid, (u, t) in members.items():
        total, done = assigned.get(uid, (0, 0))
        n_sub, med = subs.get(uid, (0, None))
        days, dist, avg_r, max_r = sums.get(uid, (0, 0.0, 0.0, 0))
        nf, nh = flags.get(uid, (0, 0))
        nc, last = checks.get(uid, (0, None))
        rows.append(
            CollectorStats(
                id=uid,
                full_name=u.full_name,
                username=u.username,
                team_id=t.id,
                team_name=t.name,
                assigned=total,
                done=done,
                coverage_pct=round(100 * done / total, 1) if total else 0.0,
                days_worked=days,
                submissions=n_sub,
                median_interview_s=med,
                avg_dwell_at_points_s=dwell.get(uid),
                distance_km=round(dist / 1000, 2),
                flags=nf,
                high_flags=nh,
                flags_per_100=round(100 * nf / n_sub, 1) if n_sub else None,
                avg_risk=round(avg_r, 1),
                max_risk=max_r,
                spot_checks=nc,
                last_spot_check=last,
            )  # fmt: skip
        )

    flag_days = {
        d: int(n)
        for d, n in db.execute(
            select(Flag.local_date, func.count())
            .where(
                Flag.collector_id.in_(ids),
                Flag.local_date >= date_from,
                Flag.local_date <= date_to,
                Flag.status != FlagStatus.dismissed,
            )
            .group_by(Flag.local_date)
        )
    }
    by_day = {
        d: (int(active), int(n or 0), float(dist or 0), float(r or 0))
        for d, active, n, dist, r in db.execute(
            select(
                CollectorDaySummary.local_date,
                func.count().filter(
                    (CollectorDaySummary.n_submissions > 0) | (CollectorDaySummary.n_gps_points > 0)
                ),
                func.sum(CollectorDaySummary.n_submissions),
                func.sum(CollectorDaySummary.distance_m),
                func.avg(cast(CollectorDaySummary.risk_score, Float)),
            )
            .where(
                CollectorDaySummary.user_id.in_(ids),
                CollectorDaySummary.local_date >= date_from,
                CollectorDaySummary.local_date <= date_to,
            )
            .group_by(CollectorDaySummary.local_date)
        )
    }
    daily = []
    d = date_from
    while d <= date_to:
        active, n, dist, r = by_day.get(d, (0, 0, 0.0, 0.0))
        daily.append(
            DailyRow(
                date=d,
                active_collectors=active,
                submissions=n,
                distance_km=round(dist / 1000, 2),
                flags=flag_days.get(d, 0),
                avg_risk=round(r, 1),
            )  # fmt: skip
        )
        d += timedelta(days=1)
    return TeamOverviewOut(date_from=date_from, date_to=date_to, collectors=rows, daily=daily)


# --- Coverage ---------------------------------------------------------------------
class AreaCoverage(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    level: AreaLevel
    points: int
    assigned: int
    done: int
    not_found_or_refused: int
    coverage_pct: float


class SupervisionCoverage(BaseModel):
    team_id: uuid.UUID
    team_name: str
    collectors: int
    spot_checked: int = Field(description="collectors with at least one spot check in the period")
    never_checked: list[str]


class CoverageOut(BaseModel):
    level: AreaLevel
    areas: list[AreaCoverage]
    supervision: list[SupervisionCoverage]


@router.get("/analytics/coverage", response_model=CoverageOut)
def coverage(
    db: DB,
    actor: StaffUser,
    date_from: date,
    date_to: date,
    level: AreaLevel = AreaLevel.ward,
    campaign_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
) -> CoverageOut:
    """Visited vs not-visited points per area, and which collectors were supervised."""
    start, end = _range(date_from, date_to)
    area = AdminArea.__table__.alias("a")
    point_area = AdminArea.__table__.alias("pa")
    stmt = (
        select(
            area.c.id,
            area.c.name,
            area.c.code,
            func.count(func.distinct(VisitPoint.id)),
            func.count(func.distinct(Assignment.visit_point_id)),
            func.count(
                func.distinct(case((Assignment.status.in_(DONE), Assignment.visit_point_id)))
            ),
            func.count(
                func.distinct(
                    case(
                        (
                            Assignment.status.in_(
                                [AssignmentStatus.not_found, AssignmentStatus.refused]
                            ),
                            Assignment.visit_point_id,
                        )
                    )
                )
            ),
        )  # fmt: skip
        .select_from(area)
        .join(point_area, point_area.c.path.startswith(area.c.path))
        .join(VisitPoint, VisitPoint.admin_area_id == point_area.c.id)
        .outerjoin(
            Assignment,
            and_(
                Assignment.visit_point_id == VisitPoint.id, Assignment.kind == AssignmentKind.visit
            ),
        )
        .where(
            area.c.level == level.value,
            VisitPoint.deleted_at.is_(None),
            area_filter(db, actor, area.c.path),
        )
        .group_by(area.c.id, area.c.name, area.c.code)
        .order_by(area.c.name)
    )
    if campaign_id:
        stmt = stmt.where(VisitPoint.campaign_id == campaign_id)
    collectors = team_collectors(db, actor, team_id)
    if team_id or actor.role == Role.supervisor:
        stmt = stmt.where(Assignment.collector_id.in_(list(collectors)))
    areas = [
        AreaCoverage(
            id=i,
            name=n,
            code=c,
            level=level,
            points=int(p),
            assigned=int(a),
            done=int(d),
            not_found_or_refused=int(nf),
            coverage_pct=round(100 * d / p, 1) if p else 0.0,
        )  # fmt: skip
        for i, n, c, p, a, d, nf in db.execute(stmt)
    ]

    checked = set(
        db.scalars(
            select(Submission.data["collector_username"].astext)
            .join(FormVersion, FormVersion.id == Submission.form_version_id)
            .join(Form, Form.id == FormVersion.form_id)
            .where(
                Form.kind == FormKind.supervision,
                Submission.ended_at >= start,
                Submission.ended_at < end,
            )
            .distinct()
        )
    )
    teams: dict[uuid.UUID, SupervisionCoverage] = {}
    for u, t in collectors.values():
        sc = teams.setdefault(
            t.id,
            SupervisionCoverage(team_id=t.id, team_name=t.name, collectors=0, spot_checked=0, never_checked=[]),
        )  # fmt: skip
        sc.collectors += 1
        if u.username in checked:
            sc.spot_checked += 1
        else:
            sc.never_checked.append(u.full_name)
    return CoverageOut(level=level, areas=areas, supervision=list(teams.values()))


# --- Battery and mobile data (NFR measurement) ------------------------------------
class DeviceUsageRow(BaseModel):
    id: uuid.UUID
    full_name: str
    team_name: str
    phone_models: list[str]
    shifts: int
    hours: float
    battery_per_hour: float | None = Field(description="% per hour, shifts without charging")
    battery_8h: float | None = Field(description="projected % for an 8-hour shift")
    mb_per_day: float | None
    gps_points_per_hour: float | None


class DeviceUsageOut(BaseModel):
    date_from: date
    date_to: date
    collectors: list[DeviceUsageRow]
    overall_battery_8h: float | None
    overall_mb_per_day: float | None
    target_battery_8h: float = 25.0


@router.get("/analytics/device-usage", response_model=DeviceUsageOut)
def device_usage(
    db: DB, actor: StaffUser, date_from: date, date_to: date, team_id: uuid.UUID | None = None
) -> DeviceUsageOut:
    """Battery drain and mobile data per collector, from the phones' end-of-shift reports."""
    from app.db.models import Device, DeviceEvent

    start, end = _range(date_from, date_to)
    members = team_collectors(db, actor, team_id)
    ids = list(members)
    events = (
        list(
            db.scalars(
                select(DeviceEvent).where(
                    DeviceEvent.user_id.in_(ids),
                    DeviceEvent.type == "shift_summary",
                    DeviceEvent.occurred_at >= start,
                    DeviceEvent.occurred_at < end,
                )
            )
        )
        if ids
        else []
    )
    models: dict[uuid.UUID, set[str]] = {}
    for uid, model in db.execute(
        select(Device.user_id, Device.model).where(Device.user_id.in_(ids))
    ):
        if model:
            models.setdefault(uid, set()).add(model)

    def num(d: dict[str, Any], k: str) -> float | None:
        v = d.get(k)
        return float(v) if isinstance(v, (int, float)) else None

    rows: list[DeviceUsageRow] = []
    all_drain: list[tuple[float, float]] = []  # (% used, hours) on battery
    all_bytes: dict[tuple[uuid.UUID, date], float] = {}
    for uid, (u, t) in members.items():
        mine = [e for e in events if e.user_id == uid]
        hours = sum((num(e.details, "minutes") or 0) / 60 for e in mine)
        drain = [
            ((num(e.details, "battery_start") or 0) - (num(e.details, "battery_end") or 0), (num(e.details, "minutes") or 0) / 60)
            for e in mine
            if not e.details.get("charged") and num(e.details, "battery_start") is not None
            and num(e.details, "battery_end") is not None and (num(e.details, "minutes") or 0) >= 30
        ]  # fmt: skip
        all_drain.extend(drain)
        per_day: dict[date, float] = {}
        gps = 0.0
        for e in mine:
            b = (num(e.details, "bytes_up") or 0) + (num(e.details, "bytes_down") or 0)
            d = e.occurred_at.date()
            per_day[d] = per_day.get(d, 0) + b
            all_bytes[(uid, d)] = all_bytes.get((uid, d), 0) + b
            gps += num(e.details, "gps_points") or 0
        rate = (
            (sum(p for p, _ in drain) / sum(h for _, h in drain))
            if drain and sum(h for _, h in drain)
            else None
        )
        rows.append(
            DeviceUsageRow(
                id=uid,
                full_name=u.full_name,
                team_name=t.name,
                phone_models=sorted(models.get(uid, set())),
                shifts=len(mine),
                hours=round(hours, 1),
                battery_per_hour=round(rate, 1) if rate is not None else None,
                battery_8h=round(rate * 8, 0) if rate is not None else None,
                mb_per_day=round(sum(per_day.values()) / len(per_day) / 1e6, 2)
                if per_day
                else None,
                gps_points_per_hour=round(gps / hours, 0) if hours else None,
            )  # fmt: skip
        )
    tot_h = sum(h for _, h in all_drain)
    overall_rate = sum(p for p, _ in all_drain) / tot_h if tot_h else None
    return DeviceUsageOut(
        date_from=date_from,
        date_to=date_to,
        collectors=rows,
        overall_battery_8h=round(overall_rate * 8, 0) if overall_rate is not None else None,
        overall_mb_per_day=round(sum(all_bytes.values()) / len(all_bytes) / 1e6, 2)
        if all_bytes
        else None,
    )
