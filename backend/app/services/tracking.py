"""Turn stored GPS points into stops and a daily summary for one collector-day.

Recomputed from scratch every time new points arrive for that day (idempotent), so
late-syncing phones simply refresh the result.
"""

import logging
import statistics
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from geoalchemy2.shape import to_shape
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.defaults import merged_config
from app.db.models import (
    Assignment,
    AssignmentStatus,
    Campaign,
    CollectorDaySummary,
    GpsPoint,
    Shift,
    Stop,
    Submission,
    Team,
    TeamMember,
    VisitPoint,
)
from app.services.geo import point_wkt
from app.services.stops import Fix, StopParams, TrackAnalysis, analyse_track, haversine_m

log = logging.getLogger(__name__)
DEFAULT_TZ = "Africa/Lagos"
SessionFactory = Callable[[], AbstractContextManager[Session]]


def user_campaign(db: Session, user_id: uuid.UUID) -> Campaign | None:
    return db.scalar(
        select(Campaign)
        .join(Team, Team.campaign_id == Campaign.id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id, Campaign.is_active.is_(True))
        .order_by(TeamMember.active_from.desc())
        .limit(1)
    )


def day_bounds(local_date: date, tz: str) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start = datetime.combine(local_date, time.min, tzinfo=z)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def local_dates(timestamps: Iterable[datetime], tz: str = DEFAULT_TZ) -> set[date]:
    z = ZoneInfo(tz)
    return {t.astimezone(z).date() for t in timestamps}


_known_partitions: set[str] = set()


def ensure_partitions(db: Session, timestamps: Iterable[datetime]) -> None:
    """Create monthly partitions for plausible dates only (last 3 months to next month).
    Points from phones with a wrong clock land in the DEFAULT partition instead of
    creating partitions for random years."""
    now = datetime.now(UTC)
    lo = (now - timedelta(days=92)).strftime("%Y-%m-01")
    hi = (now + timedelta(days=31)).strftime("%Y-%m-01")
    months = {t.astimezone(UTC).strftime("%Y-%m-01") for t in timestamps}
    for m in sorted(months - _known_partitions):
        if not lo <= m <= hi:
            continue
        try:
            with db.begin_nested():
                db.execute(text("SELECT ragoh_ensure_gps_partition(CAST(:m AS date))"), {"m": m})
        except Exception:  # e.g. rows for that month already sit in the default partition
            log.warning("Could not create GPS partition for %s; using default partition", m)
        _known_partitions.add(m)


def _params(cfg: dict[str, Any]) -> StopParams:
    s, g = cfg["stops"], cfg["gps"]
    return StopParams(
        radius_m=s["radius_m"],
        min_duration_s=s["min_duration_s"],
        merge_gap_s=s["merge_gap_s"],
        max_accuracy_m=g["max_accuracy_m"],
        gap_s=g["gap_s"],
        max_speed_mps=g["max_speed_mps"],
    )


def _gaps_inside_shifts(
    analysis: TrackAnalysis, shifts: list[Shift], now: datetime
) -> list[tuple[datetime, datetime]]:
    """A gap only counts if it falls inside a single shift (not the time between shifts)."""
    out = []
    for g in analysis.gaps:
        for s in shifts:
            end = s.ended_at or now
            if s.started_at <= g.started_at and g.ended_at <= end:
                out.append((g.started_at, g.ended_at))
                break
    return out


def process_collector_day(db: Session, user_id: uuid.UUID, local_date: date) -> CollectorDaySummary:
    campaign = user_campaign(db, user_id)
    cfg = merged_config(campaign.config if campaign else None)
    tz = campaign.timezone if campaign else DEFAULT_TZ
    start, end = day_bounds(local_date, tz)
    params = _params(cfg)

    rows = db.execute(
        select(GpsPoint.recorded_at, GpsPoint.geom, GpsPoint.accuracy_m, GpsPoint.is_mock)
        .where(
            GpsPoint.user_id == user_id, GpsPoint.recorded_at >= start, GpsPoint.recorded_at < end
        )
        .order_by(GpsPoint.recorded_at)
    ).all()
    fixes = []
    for t, geom, acc, mock in rows:
        p = to_shape(geom)
        fixes.append(Fix(t, p.y, p.x, acc, bool(mock)))
    analysis = analyse_track(fixes, params)

    # Candidate visit points: everything assigned to this collector.
    points = [
        (vp, to_shape(vp.geom))
        for vp in db.scalars(
            select(VisitPoint)
            .join(Assignment, Assignment.visit_point_id == VisitPoint.id)
            .where(Assignment.collector_id == user_id, VisitPoint.deleted_at.is_(None))
        )
    ]
    submissions = list(
        db.scalars(
            select(Submission).where(
                Submission.user_id == user_id,
                Submission.ended_at >= start,
                Submission.ended_at < end,
            )
        )
    )
    shifts = list(
        db.scalars(
            select(Shift).where(
                Shift.user_id == user_id,
                Shift.started_at < end,
                (Shift.ended_at.is_(None)) | (Shift.ended_at >= start),
            )
        )
    )
    base_places = _base_places(db, user_id, local_date, cfg)

    db.execute(delete(Stop).where(Stop.user_id == user_id, Stop.local_date == local_date))
    near_factor = cfg["stops"]["near_factor"]
    slack = timedelta(minutes=2)
    dwell_values: list[int] = []
    at_points = 0
    for s in analysis.stops:
        nearest, nearest_d = None, None
        for vp, shp in points:
            d = haversine_m(s.lat, s.lon, shp.y, shp.x)
            if nearest_d is None or d < nearest_d:
                nearest, nearest_d = vp, d
        if nearest is not None and nearest_d is not None and nearest_d <= nearest.geofence_m:
            cls = "at_assigned_point"
            at_points += 1
        elif (
            nearest is not None
            and nearest_d is not None
            and nearest_d <= nearest.geofence_m * near_factor
        ):
            cls = "near_assigned_point"
        elif any(
            haversine_m(s.lat, s.lon, la, lo) <= cfg["stops"]["base_radius_m"]
            for la, lo in base_places
        ):
            cls = "base"
        else:
            cls = "unassigned_location"
        linked = [
            str(sub.id)
            for sub in submissions
            if s.started_at - slack <= sub.ended_at <= s.ended_at + slack
        ]
        db.add(
            Stop(
                user_id=user_id,
                local_date=local_date,
                started_at=s.started_at,
                ended_at=s.ended_at,
                dwell_s=s.dwell_s,
                centroid=point_wkt(s.lon, s.lat),
                n_points=s.n_points,
                visit_point_id=nearest.id
                if cls in ("at_assigned_point", "near_assigned_point") and nearest
                else None,
                distance_to_point_m=round(nearest_d, 1) if nearest_d is not None else None,
                classification=cls,
                submission_ids=linked,
            )
        )
        dwell_values.append(s.dwell_s)

    gaps = _gaps_inside_shifts(analysis, shifts, datetime.now(UTC))
    stationary = sum(dwell_values)
    summary = db.get(CollectorDaySummary, (user_id, local_date)) or CollectorDaySummary(
        user_id=user_id, local_date=local_date
    )
    summary.distance_m = round(analysis.distance_m, 1)
    summary.n_gps_points = len(fixes)
    summary.n_mock_points = analysis.mock_points
    summary.n_stops = len(analysis.stops)
    summary.n_stops_at_points = at_points
    summary.total_dwell_s = stationary
    summary.median_dwell_s = int(statistics.median(dwell_values)) if dwell_values else 0
    summary.stationary_s = stationary
    summary.moving_s = max(0, analysis.tracked_s - stationary)
    summary.n_gaps = len(gaps)
    summary.gap_s = int(sum((b - a).total_seconds() for a, b in gaps))
    activity = [f.t for f in fixes] + [x.ended_at for x in submissions]
    summary.first_activity = min(activity) if activity else None
    summary.last_activity = max(activity) if activity else None
    summary.assigned = (
        db.scalar(
            select(func.count())
            .select_from(Assignment)
            .where(
                Assignment.collector_id == user_id,
                (Assignment.planned_date.is_(None)) | (Assignment.planned_date == local_date),
            )
        )
        or 0
    )
    summary.completed = (
        db.scalar(
            select(func.count())
            .select_from(Assignment)
            .where(
                Assignment.collector_id == user_id,
                Assignment.status == AssignmentStatus.completed,
                Assignment.status_changed_at >= start,
                Assignment.status_changed_at < end,
            )
        )
        or 0
    )
    summary.n_submissions = len(submissions)
    summary.computed_at = datetime.now(UTC)
    db.merge(summary)
    db.flush()
    return summary


def _base_places(
    db: Session, user_id: uuid.UUID, local_date: date, cfg: dict[str, Any]
) -> list[tuple[float, float]]:
    """Places with long stops on several earlier days (usually home): (lat, lon) list."""
    s = cfg["stops"]
    rows = db.execute(
        select(Stop.local_date, Stop.centroid).where(
            Stop.user_id == user_id,
            Stop.local_date < local_date,
            Stop.local_date >= local_date - timedelta(days=14),
            Stop.dwell_s >= s["base_min_dwell_s"],
        )
    ).all()
    pts = [(d, to_shape(g)) for d, g in rows]
    places: list[tuple[float, float]] = []
    for _, p in pts:
        days = {d2 for d2, p2 in pts if haversine_m(p.y, p.x, p2.y, p2.x) <= s["base_radius_m"]}
        if len(days) >= s["base_min_days"] and not any(
            haversine_m(p.y, p.x, la, lo) <= s["base_radius_m"] for la, lo in places
        ):
            places.append((p.y, p.x))
    return places


def run_processing(factory: SessionFactory, work: set[tuple[uuid.UUID, date]]) -> None:
    """Background task: recompute stops, summary and flags for each affected collector-day."""
    from app.services.flags import evaluate_day  # local import: flags depends on this module

    for user_id, d in sorted(work):
        try:
            with factory() as db:
                process_collector_day(db, user_id, d)
                evaluate_day(db, user_id, d)
                db.commit()
        except Exception:
            log.exception("Stop processing failed for %s on %s", user_id, d)
    from app.services.push import deliver_pending

    deliver_pending(factory)
