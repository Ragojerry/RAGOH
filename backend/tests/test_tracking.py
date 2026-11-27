import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.v1.forms import publish_form
from app.db.models import Assignment, FormKind, GpsPoint, PointType, VisitPoint
from app.seed_forms import household_survey
from app.services.geo import point_wkt
from app.services.stops import Fix, analyse_track
from app.services.tracking import process_collector_day
from app.simulator import SimDay, SimPoint, simulate
from tests.conftest import auth

START = datetime(2026, 11, 27, 6, 30, tzinfo=UTC)  # 07:30 in Lagos
DAY = date(2026, 11, 27)
PTS = [SimPoint(8.25 + i * 0.004, 7.10 + (i % 2) * 0.003) for i in range(5)]


def fixes(day: SimDay) -> list[Fix]:
    return [Fix(f.t, f.lat, f.lon, f.accuracy_m, f.is_mock) for f in day.fixes]


# --- Algorithm ---------------------------------------------------------------
@pytest.mark.parametrize("seed", [7, 11, 23])
@pytest.mark.parametrize("scenario", ["honest", "jitter"])
def test_one_stop_per_visit_with_accurate_dwell(scenario: str, seed: int) -> None:
    day = simulate(scenario, PTS, START, seed=seed)
    result = analyse_track(fixes(day))
    visits = result.stops[1:]  # first stop is leaving home
    assert len(visits) == len(day.visits) == 5
    for stop, truth in zip(visits, day.visits, strict=True):
        true_dwell = (truth.left - truth.arrived).total_seconds()
        assert abs(stop.dwell_s - true_dwell) <= 150, (stop, truth)
        assert abs((stop.started_at - truth.arrived).total_seconds()) <= 90


def test_phone_switched_off_is_a_gap_not_a_stop() -> None:
    day = simulate("gaps", PTS, START)
    result = analyse_track(fixes(day))
    assert len(result.gaps) == 1
    assert 44 * 60 <= result.gaps[0].duration_s <= 46 * 60
    for s in result.stops:  # no stop may span the gap
        assert not (
            s.started_at < result.gaps[0].started_at and s.ended_at > result.gaps[0].ended_at
        )


def test_cheater_never_leaves_home() -> None:
    result = analyse_track(fixes(simulate("cheater", PTS, START)))
    assert len(result.stops) == 1
    assert result.stops[0].dwell_s > 100 * 60
    assert result.distance_m < 100


def test_mock_points_are_ignored_and_counted() -> None:
    day = simulate("spoofer", PTS, START)
    result = analyse_track(fixes(day))
    assert result.stops == [] and result.used_points == 0
    assert result.mock_points == len(day.fixes)


def test_slow_walking_is_not_a_stop() -> None:
    t = START
    walk = [
        Fix(t + timedelta(seconds=10 * i), 9.0 + i * 0.00004, 7.0, 5) for i in range(90)
    ]  # 0.45 m/s for 15 min
    assert analyse_track(walk).stops == []


def test_poor_accuracy_points_are_ignored() -> None:
    t = START
    pts = [Fix(t + timedelta(seconds=60 * i), 9.0, 7.0, 500) for i in range(20)]
    assert analyse_track(pts).stops == []


def test_distance_ignores_jitter() -> None:
    honest = analyse_track(fixes(simulate("honest", PTS, START))).distance_m
    noisy = analyse_track(fixes(simulate("jitter", PTS, START))).distance_m
    assert 3000 < honest < 9000
    assert noisy < honest * 1.5


# --- End to end through the API ----------------------------------------------
@pytest.fixture
def placed(db: Session, world: dict) -> dict:  # type: ignore[type-arg]
    _, fv, _ = publish_form(
        db, world["campaign"], FormKind.survey, household_survey(), "s.xlsx", None
    )
    sim_points = []
    for i, p in enumerate(PTS):
        vp = VisitPoint(
            campaign_id=world["campaign"].id, external_id=f"T{i}", name=f"House {i}", type=PointType.household,
            admin_area_id=world["lga_a"].id, geom=point_wkt(p.lon, p.lat), geofence_m=50,
        )  # fmt: skip
        db.add(vp)
        db.flush()
        a = Assignment(visit_point_id=vp.id, collector_id=world["col_a"].id)
        db.add(a)
        db.flush()
        sim_points.append(SimPoint(p.lat, p.lon, str(a.id)))
    return {**world, "fv": fv, "sim_points": sim_points}


def push_sim(
    client: TestClient, placed: dict, scenario: str, start: datetime = START, user: str = "col_a"
) -> SimDay:  # type: ignore[type-arg]
    day = simulate(scenario, placed["sim_points"], start)
    shift_id = str(uuid.uuid4())
    loc = lambda la, lo: {"lat": la, "lon": lo, "accuracy_m": 6}  # noqa: E731
    body = {
        "device": {"id": str(uuid.uuid4())},
        "device_time": datetime.now(UTC).isoformat(),
        "shifts": [{"id": shift_id, "started_at": day.shift_start.isoformat(), "ended_at": day.shift_end.isoformat()}],  # type: ignore[union-attr]
        "gps_points": [
            {"id": str(uuid.uuid4()), "recorded_at": f.t.isoformat(), "lat": f.lat, "lon": f.lon,
             "accuracy_m": f.accuracy_m, "is_mock": f.is_mock, "shift_id": shift_id}
            for f in day.fixes
        ],
        "submissions": [
            {"id": str(uuid.uuid4()), "form_version_id": str(placed["fv"].id), "shift_id": shift_id,
             "assignment_id": s.assignment_id, "started_at": s.started_at.isoformat(), "ended_at": s.ended_at.isoformat(),
             "start_location": loc(s.lat, s.lon), "end_location": loc(s.lat, s.lon), "data": {"consent": "yes"}}
            for s in day.submissions
        ],
    }  # fmt: skip
    r = client.post("/api/v1/sync/push", json=body, headers=auth(client, user))
    assert r.status_code == 200, r.text
    assert r.json()["rejected"] == []
    return day


def get_day(client: TestClient, placed: dict, who: str = "sup_a", d: date = DAY):  # type: ignore[no-untyped-def, type-arg]
    return client.get(
        f"/api/v1/collectors/{placed['col_a'].id}/day",
        params={"date": d.isoformat()},
        headers=auth(client, who),
    )


def test_honest_day_end_to_end(client: TestClient, placed: dict) -> None:
    push_sim(client, placed, "honest")
    r = get_day(client, placed)
    assert r.status_code == 200, r.text
    data = r.json()
    at_points = [s for s in data["stops"] if s["classification"] == "at_assigned_point"]
    assert len(at_points) == 5
    assert all(len(s["submission_ids"]) == 1 for s in at_points)
    assert {s["visit_point_name"] for s in at_points} == {f"House {i}" for i in range(5)}
    summary = data["summary"]
    assert summary["n_stops_at_points"] == 5
    assert summary["n_submissions"] == 5
    assert summary["n_gaps"] == 0
    assert summary["distance_m"] > 3000
    assert 5 * 6 * 60 <= summary["total_dwell_s"] <= 5 * 17 * 60 + 600
    assert summary["moving_s"] > 0
    assert 10 < len(data["track"]) < 600  # simplified track, not every raw point


def test_cheater_day_shows_no_visits(client: TestClient, placed: dict) -> None:
    push_sim(client, placed, "cheater")
    data = get_day(client, placed).json()
    assert data["summary"]["n_stops_at_points"] == 0
    assert data["summary"]["n_submissions"] == 5
    assert [s["classification"] for s in data["stops"]] == ["unassigned_location"]
    # All five surveys were "done" during one long stay somewhere else.
    assert len(data["stops"][0]["submission_ids"]) == 5


def test_spoofer_day_counts_mock_points(client: TestClient, placed: dict) -> None:
    day = push_sim(client, placed, "spoofer")
    s = get_day(client, placed).json()["summary"]
    assert s["n_mock_points"] == len(day.fixes)
    assert s["n_stops"] == 0


def test_gap_inside_shift_is_reported(client: TestClient, placed: dict) -> None:
    push_sim(client, placed, "gaps")
    s = get_day(client, placed).json()["summary"]
    assert s["n_gaps"] == 1 and 44 * 60 <= s["gap_s"] <= 46 * 60


def test_repeated_gps_push_is_idempotent(client: TestClient, placed: dict, db: Session) -> None:
    day = simulate("honest", placed["sim_points"], START)
    pts = [
        {
            "id": str(uuid.uuid4()),
            "recorded_at": f.t.isoformat(),
            "lat": f.lat,
            "lon": f.lon,
            "accuracy_m": 5,
        }
        for f in day.fixes[:50]
    ]
    body = {
        "device": {"id": str(uuid.uuid4())},
        "device_time": datetime.now(UTC).isoformat(),
        "gps_points": pts,
    }
    h = auth(client, "col_a")
    for _ in range(3):
        assert client.post("/api/v1/sync/push", json=body, headers=h).status_code == 200
    assert db.scalar(select(func.count()).select_from(GpsPoint)) == 50


def test_points_with_a_wrong_clock_still_store(
    client: TestClient, placed: dict, db: Session
) -> None:
    pts = [
        {
            "id": str(uuid.uuid4()),
            "recorded_at": "1999-01-01T10:00:00+00:00",
            "lat": 8.25,
            "lon": 7.1,
        }
    ]
    body = {
        "device": {"id": str(uuid.uuid4())},
        "device_time": datetime.now(UTC).isoformat(),
        "gps_points": pts,
    }
    assert (
        client.post("/api/v1/sync/push", json=body, headers=auth(client, "col_a")).status_code
        == 200
    )
    assert db.scalar(text("SELECT count(*) FROM gps_points_default")) == 1


def test_home_becomes_base_after_repeated_long_stays(
    client: TestClient, placed: dict, db: Session
) -> None:
    for i in (2, 1):
        push_sim(client, placed, "cheater", start=START - timedelta(days=i))
    push_sim(client, placed, "cheater")
    process_collector_day(db, placed["col_a"].id, DAY)  # recompute now that history exists
    assert [s["classification"] for s in get_day(client, placed).json()["stops"]] == ["base"]


def test_day_view_is_scoped(client: TestClient, placed: dict) -> None:
    push_sim(client, placed, "honest")
    assert get_day(client, placed, "sup_a").status_code == 200
    assert get_day(client, placed, "col_a").status_code == 200  # own day
    assert get_day(client, placed, "manager_a").status_code == 200
    assert get_day(client, placed, "sup_b").status_code == 404
    assert get_day(client, placed, "col_b").status_code == 404


def test_recompute_and_consent(client: TestClient, placed: dict) -> None:
    push_sim(client, placed, "honest")
    r = client.post(
        f"/api/v1/collectors/{placed['col_a'].id}/day/recompute",
        params={"date": DAY.isoformat()},
        headers=auth(client, "admin"),
    )
    assert r.status_code == 200 and r.json()["n_stops_at_points"] == 5
    assert (
        client.post(
            "/api/v1/me/consent", json={"notice_version": "1"}, headers=auth(client, "col_a")
        ).status_code
        == 204
    )
