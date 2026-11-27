"""Routing: shortest walking route to a point, and the best order to visit a day's points.

Uses OSRM (road network from OpenStreetMap, FCT extract, foot profile) and VROOM (order
optimisation) when they are running. If they are not configured or not reachable, falls
back to straight-line distances with nearest-neighbour + 2-opt ordering — the same
algorithm the phone uses offline — and says so (`source`).
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.stops import haversine_m

log = logging.getLogger(__name__)

LatLon = tuple[float, float]


class RoutingUnavailable(Exception):
    pass


@dataclass
class Route:
    source: str  # "osrm" | "straight_line"
    distance_m: float
    duration_s: int
    geometry: list[list[float]]  # [[lon, lat], ...]
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PlanStop:
    index: int  # index into the input points
    arrival_offset_s: int  # seconds from departure
    leg_distance_m: float
    leg_duration_s: int
    scheduled: bool = True  # False = couldn't fit the plan's constraints; visit last


@dataclass
class DayPlan:
    source: str  # "vroom" | "heuristic"
    stops: list[PlanStop]
    distance_m: float
    duration_s: int
    geometry: list[list[float]]
    unassigned: list[int] = field(default_factory=list)


def walking_mps(speed_kmh: float) -> float:
    return speed_kmh * 1000 / 3600


# ---------------------------------------------------------------------------
# Straight-line fallback (mirrors packages/routing on the phone)
# ---------------------------------------------------------------------------
def _matrix(points: list[LatLon]) -> list[list[float]]:
    return [[haversine_m(a[0], a[1], b[0], b[1]) for b in points] for a in points]


def _tour_length(order: list[int], d: list[list[float]], start: int, end: int | None) -> float:
    path = [start, *order] + ([end] if end is not None else [])
    return sum(d[a][b] for a, b in zip(path, path[1:], strict=False))


def heuristic_order(
    start: LatLon, points: list[LatLon], return_to_start: bool = False
) -> list[int]:
    """Nearest neighbour, then 2-opt improvement. Returns indices into `points`."""
    if not points:
        return []
    nodes = [start, *points]
    d = _matrix(nodes)
    remaining = set(range(1, len(nodes)))
    order: list[int] = []
    cur = 0
    while remaining:
        nxt = min(remaining, key=lambda j: d[cur][j])
        order.append(nxt)
        remaining.remove(nxt)
        cur = nxt
    end = 0 if return_to_start else None
    improved = True
    while improved:
        improved = False
        for i in range(len(order) - 1):
            for k in range(i + 1, len(order)):
                candidate = order[:i] + order[i : k + 1][::-1] + order[k + 1 :]
                if _tour_length(candidate, d, 0, end) < _tour_length(order, d, 0, end) - 1e-6:
                    order = candidate
                    improved = True
    return [i - 1 for i in order]


def heuristic_plan(
    start: LatLon,
    points: list[LatLon],
    speed_kmh: float,
    service_s: int,
    return_to_start: bool = False,
) -> DayPlan:
    mps = walking_mps(speed_kmh)
    order = heuristic_order(start, points, return_to_start)
    stops: list[PlanStop] = []
    t, total, prev = 0, 0.0, start
    for idx in order:
        p = points[idx]
        leg = haversine_m(prev[0], prev[1], p[0], p[1])
        leg_s = int(leg / mps)
        t += leg_s
        stops.append(PlanStop(idx, t, round(leg, 1), leg_s))
        t += service_s
        total += leg
        prev = p
    geometry = [[start[1], start[0]]] + [[points[s.index][1], points[s.index][0]] for s in stops]
    if return_to_start and stops:
        back = haversine_m(prev[0], prev[1], start[0], start[1])
        total += back
        t += int(back / mps)
        geometry.append([start[1], start[0]])
    return DayPlan("heuristic", stops, round(total, 1), t, geometry)


def straight_route(a: LatLon, b: LatLon, speed_kmh: float) -> Route:
    d = haversine_m(a[0], a[1], b[0], b[1])
    return Route(
        "straight_line", round(d, 1), int(d / walking_mps(speed_kmh)), [[a[1], a[0]], [b[1], b[0]]]
    )


# ---------------------------------------------------------------------------
# OSRM / VROOM
# ---------------------------------------------------------------------------
def _client() -> httpx.Client:
    return httpx.Client(timeout=get_settings().routing_timeout_s)


def decode_polyline(encoded: str, precision: int = 5) -> list[list[float]]:
    """Google encoded polyline → [[lon, lat], ...]."""
    coords: list[list[float]] = []
    index = lat = lon = 0
    factor = 10**precision
    while index < len(encoded):
        for is_lat in (True, False):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lat:
                lat += delta
            else:
                lon += delta
        coords.append([lon / factor, lat / factor])
    return coords


def osrm_route(a: LatLon, b: LatLon, client: httpx.Client | None = None) -> Route:
    base = get_settings().osrm_url
    if not base:
        raise RoutingUnavailable("OSRM not configured")
    url = f"{base.rstrip('/')}/route/v1/foot/{a[1]},{a[0]};{b[1]},{b[0]}"
    try:
        with client or _client() as c:
            r = c.get(url, params={"overview": "full", "geometries": "geojson", "steps": "true"})
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RoutingUnavailable(str(exc)) from None
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingUnavailable(data.get("message") or data.get("code") or "no route")
    route = data["routes"][0]
    steps = []
    for leg in route.get("legs", []):
        for st in leg.get("steps", []):
            m = st.get("maneuver", {})
            steps.append(
                {
                    "instruction": _instruction(
                        m.get("type", ""), m.get("modifier"), st.get("name") or ""
                    ),
                    "distance_m": round(st.get("distance", 0), 1),
                    "duration_s": int(st.get("duration", 0)),
                }
            )
    return Route(
        "osrm",
        round(route["distance"], 1),
        int(route["duration"]),
        route["geometry"]["coordinates"],
        steps,
    )


def _instruction(kind: str, modifier: str | None, name: str) -> str:
    road = f" onto {name}" if name else ""
    if kind == "depart":
        return f"Start{(' on ' + name) if name else ''}"
    if kind == "arrive":
        return "Arrive at the point"
    if kind in ("turn", "end of road", "fork", "on ramp", "off ramp"):
        return f"Turn {modifier or ''}{road}".replace("  ", " ").strip()
    if kind == "roundabout":
        return f"Take the roundabout{road}"
    return f"Continue{road}"


def vroom_plan(
    start: LatLon,
    points: list[LatLon],
    service_s: int,
    return_to_start: bool,
    priorities: list[int] | None = None,
    time_windows: list[tuple[int, int] | None] | None = None,
    now_s: int = 0,
    client: httpx.Client | None = None,
) -> DayPlan:
    base = get_settings().vroom_url
    if not base:
        raise RoutingUnavailable("VROOM not configured")
    vehicle: dict[str, Any] = {
        "id": 1,
        "profile": "foot",
        "start": [start[1], start[0]],
        "time_window": [now_s, 86400 * 2],
    }
    if return_to_start:
        vehicle["end"] = [start[1], start[0]]
    jobs = []
    for i, p in enumerate(points):
        job: dict[str, Any] = {"id": i + 1, "location": [p[1], p[0]], "service": service_s}
        if priorities:
            job["priority"] = max(0, min(100, priorities[i]))
        if time_windows and time_windows[i]:
            job["time_windows"] = [list(time_windows[i])]  # type: ignore[arg-type]
        jobs.append(job)
    payload = {"vehicles": [vehicle], "jobs": jobs, "options": {"g": True}}
    try:
        with client or _client() as c:
            r = c.post(base.rstrip("/") + "/", json=payload)
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RoutingUnavailable(str(exc)) from None
    if data.get("code") != 0 or not data.get("routes"):
        raise RoutingUnavailable(data.get("error") or "no plan")
    route = data["routes"][0]
    stops: list[PlanStop] = []
    prev_dist, prev_dur = 0.0, 0
    depart = None
    for st in route["steps"]:
        if st["type"] == "start":
            depart = st.get("arrival", now_s)
            prev_dist, prev_dur = st.get("distance", 0.0), st.get("duration", 0)
        elif st["type"] == "job":
            dist, dur = st.get("distance", 0.0), st.get("duration", 0)
            stops.append(
                PlanStop(
                    st["job"] - 1,
                    int(st["arrival"] - (depart or now_s)),
                    round(dist - prev_dist, 1),
                    int(dur - prev_dur),
                )
            )
            prev_dist, prev_dur = dist, dur
    geometry = decode_polyline(route["geometry"]) if route.get("geometry") else []
    unassigned = [u["id"] - 1 for u in data.get("unassigned", [])]
    return DayPlan(
        "vroom",
        stops,
        round(route.get("distance", 0.0), 1),
        int(route.get("duration", 0) + route.get("service", 0)),
        geometry,
        unassigned,
    )


def plan_day(
    start: LatLon,
    points: list[LatLon],
    speed_kmh: float,
    service_s: int,
    return_to_start: bool = False,
    priorities: list[int] | None = None,
    time_windows: list[tuple[int, int] | None] | None = None,
    now_s: int = 0,
) -> DayPlan:
    try:
        plan = vroom_plan(
            start, points, service_s, return_to_start, priorities, time_windows, now_s
        )
        # Points VROOM couldn't fit (e.g. time windows already passed) still get visited, last.
        for idx in plan.unassigned:
            last = plan.stops[-1].arrival_offset_s + service_s if plan.stops else 0
            plan.stops.append(PlanStop(idx, last, 0.0, 0, scheduled=False))
        return plan
    except RoutingUnavailable as exc:
        log.info("VROOM unavailable (%s); using heuristic ordering", exc)
        return heuristic_plan(start, points, speed_kmh, service_s, return_to_start)


def route_to(a: LatLon, b: LatLon, speed_kmh: float) -> Route:
    try:
        return osrm_route(a, b)
    except RoutingUnavailable as exc:
        log.info("OSRM unavailable (%s); using straight line", exc)
        return straight_route(a, b, speed_kmh)
