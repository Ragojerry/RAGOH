"""Tracking results per collector-day: summary, stops, track, submissions, phone events."""

import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from geoalchemy2.shape import to_shape
from shapely.geometry import LineString
from sqlalchemy import select

from app.core.deps import DB, CurrentUser, ManagerOrAdmin
from app.db.models import (
    CollectorDaySummary,
    DeviceEvent,
    GpsPoint,
    Role,
    Stop,
    Submission,
    User,
    VisitPoint,
)
from app.schemas import (
    CollectorDayOut,
    ConsentIn,
    DaySubmissionOut,
    DaySummaryOut,
    DeviceEventOut,
    StopOut,
    UserOut,
)
from app.services.audit import audit
from app.services.scope import visible_user_ids_condition
from app.services.tracking import DEFAULT_TZ, day_bounds, process_collector_day, user_campaign

router = APIRouter(tags=["tracking"])

# ~10 m in degrees near the equator; enough to keep a day's track small on slow networks.
SIMPLIFY_DEG = 0.0001


def _visible_user(db: DB, actor: User, user_id: uuid.UUID) -> User:
    if actor.role == Role.collector and actor.id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user = db.scalar(select(User).where(User.id == user_id, visible_user_ids_condition(db, actor)))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/collectors/{user_id}/day", response_model=CollectorDayOut)
def collector_day(
    user_id: uuid.UUID, db: DB, actor: CurrentUser, day: date = Query(alias="date")
) -> CollectorDayOut:
    user = _visible_user(db, actor, user_id)
    campaign = user_campaign(db, user.id)
    start, end = day_bounds(day, campaign.timezone if campaign else DEFAULT_TZ)

    names = {}
    stops = []
    for s, vp_name in db.execute(
        select(Stop, VisitPoint.name)
        .outerjoin(VisitPoint, VisitPoint.id == Stop.visit_point_id)
        .where(Stop.user_id == user.id, Stop.local_date == day)
        .order_by(Stop.started_at)
    ):
        c = to_shape(s.centroid)
        names[s.visit_point_id] = vp_name
        stops.append(
            StopOut(
                id=s.id,
                started_at=s.started_at,
                ended_at=s.ended_at,
                dwell_s=s.dwell_s,
                lat=c.y,
                lon=c.x,
                n_points=s.n_points,
                classification=s.classification,
                visit_point_id=s.visit_point_id,
                visit_point_name=vp_name,
                distance_to_point_m=s.distance_to_point_m,
                submission_ids=s.submission_ids,
            )  # fmt: skip
        )

    subs = []
    for sub in db.scalars(
        select(Submission)
        .where(
            Submission.user_id == user.id, Submission.ended_at >= start, Submission.ended_at < end
        )
        .order_by(Submission.ended_at)
    ):
        g = to_shape(sub.end_geom) if sub.end_geom is not None else None
        subs.append(
            DaySubmissionOut(
                id=sub.id,
                started_at=sub.started_at,
                ended_at=sub.ended_at,
                duration_s=sub.duration_s,
                visit_point_id=sub.visit_point_id,
                lat=g.y if g else None,
                lon=g.x if g else None,
            )  # fmt: skip
        )

    rows = db.execute(
        select(GpsPoint.recorded_at, GpsPoint.geom)
        .where(
            GpsPoint.user_id == user.id,
            GpsPoint.recorded_at >= start,
            GpsPoint.recorded_at < end,
            GpsPoint.is_mock.is_(False),
        )
        .order_by(GpsPoint.recorded_at)
    ).all()
    coords = [(to_shape(g).x, to_shape(g).y, t.timestamp()) for t, g in rows]
    track: list[list[float]] = []
    if len(coords) > 2:
        simple = LineString([(x, y) for x, y, _ in coords]).simplify(
            SIMPLIFY_DEG, preserve_topology=False
        )
        keep = {(round(x, 7), round(y, 7)) for x, y in simple.coords}
        track = [
            [round(x, 6), round(y, 6), int(t)]
            for x, y, t in coords
            if (round(x, 7), round(y, 7)) in keep
        ]
    else:
        track = [[round(x, 6), round(y, 6), int(t)] for x, y, t in coords]

    events = [
        DeviceEventOut(type=e.type, occurred_at=e.occurred_at, details=e.details)
        for e in db.scalars(
            select(DeviceEvent)
            .where(
                DeviceEvent.user_id == user.id,
                DeviceEvent.occurred_at >= start,
                DeviceEvent.occurred_at < end,
            )
            .order_by(DeviceEvent.occurred_at)
        )
    ]
    summary = db.get(CollectorDaySummary, (user.id, day))
    if actor.id != user.id:
        audit(
            db,
            "track_view",
            actor_id=actor.id,
            entity="user",
            entity_id=user.id,
            details={"date": day.isoformat()},
        )
        db.commit()
    return CollectorDayOut(
        user=UserOut.model_validate(user),
        date=day,
        summary=DaySummaryOut.model_validate(summary) if summary else None,
        stops=stops,
        submissions=subs,
        events=events,
        track=track,
    )


@router.post("/collectors/{user_id}/day/recompute", response_model=DaySummaryOut)
def recompute_day(
    user_id: uuid.UUID, db: DB, actor: ManagerOrAdmin, day: date = Query(alias="date")
) -> CollectorDaySummary:
    user = _visible_user(db, actor, user_id)
    summary = process_collector_day(db, user.id, day)
    db.commit()
    return summary


@router.post("/me/consent", status_code=status.HTTP_204_NO_CONTENT)
def record_consent(body: ConsentIn, db: DB, user: CurrentUser) -> None:
    from app.db.models import Consent

    db.add(Consent(user_id=user.id, notice_version=body.notice_version))
    audit(db, "consent", actor_id=user.id, details={"notice_version": body.notice_version})
    db.commit()
