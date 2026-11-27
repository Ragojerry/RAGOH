import json
import random

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Assignment, AssignmentStatus, PointType, RoutePlanned, VisitPoint
from app.services import routing
from app.services.geo import point_wkt
from app.services.routing import decode_polyline, heuristic_order, heuristic_plan
from app.services.stops import haversine_m
from tests.conftest import auth


# --- Pure algorithms ---------------------------------------------------------
def test_points_on_a_line_are_visited_in_order() -> None:
    line = [(9.0 + i * 0.002, 7.0) for i in range(8)]
    shuffled = line[:]
    random.Random(1).shuffle(shuffled)  # noqa: S311
    order = heuristic_order((8.998, 7.0), shuffled)
    assert [shuffled[i] for i in order] == line


def test_two_opt_removes_crossings() -> None:
    # Four corners of a square: a crossing tour is longer than the perimeter.
    sq = [(9.0, 7.0), (9.01, 7.01), (9.0, 7.01), (9.01, 7.0)]
    order = heuristic_order((9.0, 7.0), sq, return_to_start=True)
    tour = [(9.0, 7.0)] + [sq[i] for i in order] + [(9.0, 7.0)]
    length = sum(haversine_m(*a, *b) for a, b in zip(tour, tour[1:], strict=False))
    side = haversine_m(9.0, 7.0, 9.01, 7.0)
    assert length < side * 4.2  # perimeter, not diagonals


def test_heuristic_plan_times_and_distances() -> None:
    plan = heuristic_plan(
        (9.0, 7.0), [(9.0036, 7.0)], speed_kmh=3.6, service_s=600
    )  # ~400 m at 1 m/s
    assert plan.source == "heuristic"
    assert 390 < plan.stops[0].leg_distance_m < 410
    assert 390 < plan.stops[0].arrival_offset_s < 410
    assert plan.duration_s == plan.stops[0].arrival_offset_s + 600


def test_decode_polyline() -> None:
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [
        [-120.2, 38.5],
        [-120.95, 40.7],
        [-126.453, 43.252],
    ]


# --- OSRM / VROOM clients (mocked) -------------------------------------------
OSRM_OK = {
    "code": "Ok",
    "routes": [{
        "distance": 812.4, "duration": 650.2,
        "geometry": {"type": "LineString", "coordinates": [[7.1, 8.2], [7.101, 8.201], [7.103, 8.202]]},
        "legs": [{"steps": [
            {"maneuver": {"type": "depart"}, "name": "Ahmadu Bello Way", "distance": 500, "duration": 400},
            {"maneuver": {"type": "turn", "modifier": "left"}, "name": "Garki Road", "distance": 312.4, "duration": 250.2},
            {"maneuver": {"type": "arrive"}, "name": "", "distance": 0, "duration": 0},
        ]}],
    }],
}  # fmt: skip


def vroom_ok(n_jobs: int, unassigned: list[int] | None = None) -> dict:  # type: ignore[type-arg]
    steps = [{"type": "start", "arrival": 28800, "distance": 0, "duration": 0}]
    order = [j for j in range(n_jobs, 0, -1) if j not in (unassigned or [])]
    for k, job in enumerate(order, start=1):
        steps.append(
            {
                "type": "job",
                "job": job,
                "arrival": 28800 + k * 900,
                "distance": k * 300.0,
                "duration": k * 300,
            }
        )
    return {
        "code": 0,
        "routes": [{"steps": steps, "distance": len(order) * 300.0, "duration": len(order) * 300, "service": len(order) * 600,
                    "geometry": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"}],
        "unassigned": [{"id": u} for u in (unassigned or [])],
    }  # fmt: skip


@pytest.fixture
def engines(monkeypatch):  # type: ignore[no-untyped-def]
    """Point the app at mocked OSRM/VROOM. Returns the list of requests made."""
    calls: list[httpx.Request] = []
    state = {"vroom_unassigned": []}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "osrm":
            return httpx.Response(200, json=OSRM_OK)
        body = json.loads(request.content)
        return httpx.Response(200, json=vroom_ok(len(body["jobs"]), state["vroom_unassigned"]))

    s = get_settings()
    monkeypatch.setattr(s, "osrm_url", "http://osrm:5000")
    monkeypatch.setattr(s, "vroom_url", "http://vroom:3000")
    monkeypatch.setattr(
        routing, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    return {"calls": calls, "state": state}


@pytest.fixture
def no_engines(monkeypatch):  # type: ignore[no-untyped-def]
    s = get_settings()
    monkeypatch.setattr(s, "osrm_url", None)
    monkeypatch.setattr(s, "vroom_url", None)


def test_osrm_route_is_parsed_with_readable_steps(engines) -> None:  # type: ignore[no-untyped-def]
    r = routing.osrm_route((8.2, 7.1), (8.202, 7.103))
    assert r.source == "osrm" and r.distance_m == 812.4 and r.duration_s == 650
    assert [s["instruction"] for s in r.steps] == [
        "Start on Ahmadu Bello Way",
        "Turn left onto Garki Road",
        "Arrive at the point",
    ]
    assert "/route/v1/foot/7.1,8.2;7.103,8.202" in str(engines["calls"][0].url)


def test_unreachable_engines_fall_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    s = get_settings()
    monkeypatch.setattr(s, "osrm_url", "http://osrm:5000")
    monkeypatch.setattr(s, "vroom_url", "http://vroom:3000")
    monkeypatch.setattr(
        routing, "_client", lambda: httpx.Client(transport=httpx.MockTransport(boom))
    )
    assert routing.route_to((9.0, 7.0), (9.01, 7.0), 4).source == "straight_line"
    assert routing.plan_day((9.0, 7.0), [(9.01, 7.0)], 4, 600).source == "heuristic"


# --- API ---------------------------------------------------------------------
@pytest.fixture
def todo(db: Session, world: dict) -> dict:  # type: ignore[type-arg]
    rows = []
    for i in range(4):
        vp = VisitPoint(
            campaign_id=world["campaign"].id, external_id=f"R{i}", name=f"House R{i}", type=PointType.household,
            admin_area_id=world["lga_a"].id, geom=point_wkt(7.10 + i * 0.003, 8.20), geofence_m=50, priority=i,
        )  # fmt: skip
        db.add(vp)
        db.flush()
        a = Assignment(visit_point_id=vp.id, collector_id=world["col_a"].id)
        db.add(a)
        db.flush()
        rows.append((a, vp))
    rows[3][0].status = AssignmentStatus.completed
    db.flush()
    return {**world, "rows": rows}


def test_route_to_point_falls_back_to_straight_line(
    client: TestClient, todo: dict, no_engines
) -> None:  # type: ignore[no-untyped-def]
    vp = todo["rows"][0][1]
    r = client.get(
        "/api/v1/routes/to",
        params={"visit_point_id": str(vp.id), "lat": 8.21, "lon": 7.10},
        headers=auth(client, "col_a"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "straight_line"
    assert 1000 < r.json()["distance_m"] < 1200


def test_route_to_point_uses_osrm_when_available(client: TestClient, todo: dict, engines) -> None:  # type: ignore[no-untyped-def]
    vp = todo["rows"][0][1]
    r = client.get(
        "/api/v1/routes/to",
        params={"visit_point_id": str(vp.id), "lat": 8.21, "lon": 7.10},
        headers=auth(client, "col_a"),
    )
    assert r.json()["source"] == "osrm" and len(r.json()["steps"]) == 3


def test_collector_cannot_route_to_someone_elses_point(
    client: TestClient, todo: dict, no_engines
) -> None:  # type: ignore[no-untyped-def]
    vp = todo["rows"][0][1]
    r = client.get(
        "/api/v1/routes/to",
        params={"visit_point_id": str(vp.id), "lat": 8.2, "lon": 7.1},
        headers=auth(client, "col_b"),
    )
    assert r.status_code == 404


def test_day_plan_heuristic_orders_open_visits_and_is_saved(
    client: TestClient, todo: dict, db: Session, no_engines
) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/routes/day-plan", json={"lat": 8.20, "lon": 7.095}, headers=auth(client, "col_a")
    )
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["source"] == "heuristic"
    assert [s["name"] for s in plan["stops"]] == ["House R0", "House R1", "House R2"]  # R3 is done
    assert plan["stops"][0]["order"] == 1 and plan["distance_m"] > 0
    assert db.scalar(select(func.count()).select_from(RoutePlanned)) == 1


def test_day_plan_uses_vroom_and_appends_unfittable_visits(
    client: TestClient, todo: dict, engines
) -> None:  # type: ignore[no-untyped-def]
    engines["state"]["vroom_unassigned"] = [2]
    plan = client.post(
        "/api/v1/routes/day-plan",
        json={"lat": 8.2, "lon": 7.095, "return_to_start": True},
        headers=auth(client, "col_a"),
    ).json()
    assert plan["source"] == "vroom"
    assert [s["scheduled"] for s in plan["stops"]] == [True, True, False]
    sent = json.loads(engines["calls"][-1].content)
    assert sent["vehicles"][0]["end"] == sent["vehicles"][0]["start"]
    assert {j["priority"] for j in sent["jobs"]} == {0, 1, 2}
    assert all(j["service"] == 600 for j in sent["jobs"])


def test_day_plan_with_nothing_open(client: TestClient, world: dict, no_engines) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/routes/day-plan", json={"lat": 8.2, "lon": 7.1}, headers=auth(client, "col_b")
    )
    assert r.status_code == 404


def test_map_style_appears_once_tiles_exist(
    client: TestClient, world: dict, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "tiles_dir", str(tmp_path))
    assert client.get("/api/v1/map/style.json").status_code == 404
    (tmp_path / "fct.pmtiles").write_bytes(b"PMTiles")
    (tmp_path / "style-layers.json").write_text(
        json.dumps([{"id": "background", "type": "background"}])
    )
    r = client.get(
        "/api/v1/map/style.json",
        headers={"x-forwarded-proto": "https", "host": "13-48-1-2.sslip.io"},
    )
    assert r.status_code == 200
    style = r.json()
    assert style["sources"]["protomaps"]["tiles"] == [
        "https://13-48-1-2.sslip.io/tiles/fct/{z}/{x}/{y}.mvt"
    ]
    assert style["glyphs"].startswith("https://13-48-1-2.sslip.io/map-assets/fonts/")
    assert style["layers"][0]["id"] == "background"
