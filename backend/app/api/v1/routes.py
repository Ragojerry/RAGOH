"""Walking routes, day plans and the offline-map style."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.defaults import merged_config
from app.core.deps import DB, CurrentUser
from app.db.models import Assignment, AssignmentStatus, RoutePlanned, VisitPoint
from app.services.geo import WKTElement, lonlat
from app.services.routing import plan_day, route_to
from app.services.tracking import DEFAULT_TZ, user_campaign

router = APIRouter(tags=["routing"])

OPEN_STATUSES = (AssignmentStatus.pending, AssignmentStatus.en_route, AssignmentStatus.arrived)


class RouteOut(BaseModel):
    source: str = Field(description="osrm = road route; straight_line = estimate (routing offline)")
    distance_m: float
    duration_s: int
    geometry: list[list[float]]
    steps: list[dict[str, Any]]


class DayPlanIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    return_to_start: bool | None = None
    assignment_ids: list[uuid.UUID] | None = Field(
        default=None, description="Default: all open assignments"
    )


class PlanStopOut(BaseModel):
    order: int
    assignment_id: uuid.UUID
    visit_point_id: uuid.UUID
    name: str
    lat: float
    lon: float
    arrival_offset_s: int
    leg_distance_m: float
    leg_duration_s: int
    scheduled: bool


class DayPlanOut(BaseModel):
    plan_id: uuid.UUID
    source: str = Field(
        description="vroom = optimised on roads; heuristic = straight-line estimate"
    )
    distance_m: float
    duration_s: int
    stops: list[PlanStopOut]
    geometry: list[list[float]]


def _cfg(db: DB, user_id: uuid.UUID) -> tuple[dict[str, Any], str]:
    c = user_campaign(db, user_id)
    return merged_config(c.config if c else None), (c.timezone if c else DEFAULT_TZ)


@router.get("/routes/to", response_model=RouteOut)
def walking_route(
    db: DB,
    user: CurrentUser,
    visit_point_id: uuid.UUID,
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
) -> RouteOut:
    vp = db.get(VisitPoint, visit_point_id)
    mine = db.scalar(
        select(Assignment.id).where(
            Assignment.visit_point_id == visit_point_id, Assignment.collector_id == user.id
        )
    )
    if vp is None or (mine is None and user.role.value == "collector"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visit point not found")
    cfg, _ = _cfg(db, user.id)
    plon, plat = lonlat(vp.geom)
    r = route_to((lat, lon), (plat, plon), cfg["routing"]["walking_speed_kmh"])
    return RouteOut(
        source=r.source,
        distance_m=r.distance_m,
        duration_s=r.duration_s,
        geometry=r.geometry,
        steps=r.steps,
    )


@router.post("/routes/day-plan", response_model=DayPlanOut)
def day_plan(body: DayPlanIn, db: DB, user: CurrentUser) -> DayPlanOut:
    cfg, tz = _cfg(db, user.id)
    stmt = (
        select(Assignment, VisitPoint)
        .join(VisitPoint, VisitPoint.id == Assignment.visit_point_id)
        .where(
            Assignment.collector_id == user.id,
            Assignment.status.in_(OPEN_STATUSES),
            VisitPoint.deleted_at.is_(None),
        )
    )
    if body.assignment_ids:
        stmt = stmt.where(Assignment.id.in_(body.assignment_ids))
    rows = list(db.execute(stmt).tuples().all())
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open visits to plan")

    now_local = datetime.now(ZoneInfo(tz))
    now_s = now_local.hour * 3600 + now_local.minute * 60
    coords: list[tuple[float, float]] = []
    priorities: list[int] = []
    windows: list[tuple[int, int] | None] = []
    for _, vp in rows:
        x, y = lonlat(vp.geom)
        coords.append((y, x))
        priorities.append(vp.priority)
        if vp.window_start and vp.window_end:
            windows.append(
                (
                    vp.window_start.hour * 3600 + vp.window_start.minute * 60,
                    vp.window_end.hour * 3600 + vp.window_end.minute * 60,
                )
            )
        else:
            windows.append(None)
    r = cfg["routing"]
    ret = body.return_to_start if body.return_to_start is not None else r["return_to_start"]
    plan = plan_day(
        (body.lat, body.lon),
        coords,
        r["walking_speed_kmh"],
        r["service_s"],
        ret,
        priorities,
        windows,
        now_s,
    )

    stops = []
    for n, st in enumerate(plan.stops, start=1):
        a, vp = rows[st.index]
        stops.append(
            PlanStopOut(
                order=n,
                assignment_id=a.id,
                visit_point_id=vp.id,
                name=vp.name,
                lat=coords[st.index][0],
                lon=coords[st.index][1],
                arrival_offset_s=st.arrival_offset_s,
                leg_distance_m=st.leg_distance_m,
                leg_duration_s=st.leg_duration_s,
                scheduled=st.scheduled,
            )  # fmt: skip
        )
    line = None
    if len(plan.geometry) >= 2:
        line = WKTElement(
            "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in plan.geometry) + ")", srid=4326
        )
    record = RoutePlanned(
        user_id=user.id,
        local_date=now_local.date(),
        mode=r["mode"],
        source=plan.source,
        stop_order=[str(s.assignment_id) for s in stops],
        geometry=line,
        distance_m=plan.distance_m,
        duration_s=plan.duration_s,
    )
    db.add(record)
    db.commit()
    return DayPlanOut(
        plan_id=record.id,
        source=plan.source,
        distance_m=plan.distance_m,
        duration_s=plan.duration_s,
        stops=stops,
        geometry=plan.geometry,
    )


@router.get("/map/style.json")
def map_style(request: Request) -> dict[str, Any]:
    """MapLibre style for the self-hosted FCT map (used online and for offline downloads).
    404 until the routing-data workflow has produced the tiles; the app then falls back to
    OpenStreetMap's online tiles."""
    s = get_settings()
    layers_file = Path(s.tiles_dir) / "style-layers.json"
    tiles_file = Path(s.tiles_dir) / f"{s.tileset}.pmtiles"
    if not layers_file.exists() or not tiles_file.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Offline map not prepared yet")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    base = f"{proto}://{host}"
    return {
        "version": 8,
        "name": "RAGOH FCT",
        "glyphs": f"{base}/map-assets/fonts/{{fontstack}}/{{range}}.pbf",
        "sprite": f"{base}/map-assets/sprites/v4/light",
        "sources": {
            "protomaps": {
                "type": "vector",
                "tiles": [f"{base}/tiles/{s.tileset}/{{z}}/{{x}}/{{y}}.mvt"],
                "minzoom": 0,
                "maxzoom": 15,
                "attribution": "© OpenStreetMap contributors · Protomaps",
            }
        },
        "layers": json.loads(layers_file.read_text()),
        "metadata": {
            "generated_at": datetime.fromtimestamp(tiles_file.stat().st_mtime, UTC).isoformat()
        },
    }
