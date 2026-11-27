"""Phase 7: dashboard data — boundaries, points, team analytics, coverage, exports."""

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.forms import publish_form
from app.core.config import get_settings
from app.db.models import AuditLog, FormKind
from app.seed_forms import supervision_checklist
from tests.conftest import auth
from tests.test_flags import push_day
from tests.test_tracking import DAY, START, placed  # noqa: F401 — fixture

API = "/api/v1"
RANGE = {"date_from": DAY.isoformat(), "date_to": DAY.isoformat()}


@pytest.fixture
def worked(client: TestClient, placed: dict, db: Session) -> dict:  # noqa: F811
    """col_a's cheating day is synced, and sup_a did one spot check of col_a."""
    push_day(client, placed, "cheater")
    publish_form(
        db, placed["campaign"], FormKind.supervision, supervision_checklist(), "c.xlsx", None
    )
    form = next(
        f for f in client.get(f"{API}/sync/pull", headers=auth(client, "sup_a")).json()["forms"]
        if f["kind"] == "supervision"
    )  # fmt: skip
    t = START + timedelta(hours=3)
    r = client.post(
        f"{API}/sync/push",
        json={"device": {"id": str(uuid.uuid4())}, "device_time": datetime.now(UTC).isoformat(),
              "submissions": [{"id": str(uuid.uuid4()), "form_version_id": form["version_id"],
                               "started_at": (t - timedelta(minutes=10)).isoformat(), "ended_at": t.isoformat(),
                               "data": {"collector_username": "col_a", "on_time": "yes"}}]},
        headers=auth(client, "sup_a"),
    )  # fmt: skip
    assert r.json()["rejected"] == []
    return placed


def test_area_boundaries_are_scoped(client: TestClient, world: dict) -> None:
    def names(user: str) -> set[str]:
        r = client.get(
            f"{API}/map/areas.geojson", params={"level": "lga"}, headers=auth(client, user)
        )
        assert r.status_code == 200
        return {f["properties"]["name"] for f in r.json()["features"]}

    assert names("admin") == {"LGA A", "LGA B"}
    assert names("sup_a") == {"LGA A"}
    assert names("manager_a") == {"LGA A"}
    assert client.get(f"{API}/map/areas.geojson", headers=auth(client, "col_a")).status_code == 403


def test_points_layer_shows_status_and_collector(client: TestClient, worked: dict) -> None:
    r = client.get(f"{API}/map/points.geojson", headers=auth(client, "sup_a"))
    feats = r.json()["features"]
    assert len(feats) == 5
    assert all(f["properties"]["done"] and f["properties"]["collector"] == "Col_A" for f in feats)
    assert (
        client.get(f"{API}/map/points.geojson", headers=auth(client, "sup_b")).json()["features"]
        == []
    )


def test_team_overview_compares_collectors(client: TestClient, worked: dict) -> None:
    r = client.get(f"{API}/analytics/team", params=RANGE, headers=auth(client, "sup_a"))
    assert r.status_code == 200, r.text
    body = r.json()
    (c,) = body["collectors"]  # only sup_a's team
    assert c["username"] == "col_a"
    assert (c["assigned"], c["done"], c["coverage_pct"]) == (5, 5, 100.0)
    assert c["submissions"] == 5 and c["median_interview_s"] and c["median_interview_s"] < 300
    assert c["flags"] >= 10 and c["high_flags"] >= 10 and c["flags_per_100"] >= 200
    assert c["max_risk"] >= 80 and c["days_worked"] == 1
    assert c["spot_checks"] == 1 and c["last_spot_check"]
    (day,) = body["daily"]
    assert day["submissions"] == 5 and day["active_collectors"] == 1 and day["flags"] == c["flags"]

    other = client.get(f"{API}/analytics/team", params=RANGE, headers=auth(client, "sup_b")).json()
    assert [x["username"] for x in other["collectors"]] == ["col_b"]
    too_long = {"date_from": "2026-01-01", "date_to": "2026-11-27"}
    assert (
        client.get(
            f"{API}/analytics/team", params=too_long, headers=auth(client, "sup_a")
        ).status_code
        == 422
    )


def test_coverage_by_area_and_supervision(client: TestClient, worked: dict) -> None:
    r = client.get(
        f"{API}/analytics/coverage", params={**RANGE, "level": "lga"}, headers=auth(client, "admin")
    )
    assert r.status_code == 200, r.text
    by_name = {a["name"]: a for a in r.json()["areas"]}
    assert by_name["LGA A"]["points"] == 5 and by_name["LGA A"]["done"] == 5
    assert by_name["LGA A"]["coverage_pct"] == 100.0
    sup = {s["team_name"]: s for s in r.json()["supervision"]}
    assert sup["Team A"]["spot_checked"] == 1 and sup["Team A"]["never_checked"] == []
    assert sup["Team B"]["never_checked"] == ["Col_B"]


def test_manager_live_view_is_limited_to_their_geography(client: TestClient, world: dict) -> None:
    r = client.get(f"{API}/team/live", headers=auth(client, "manager_a"))
    assert [c["username"] for c in r.json()["collectors"]] == ["col_a"]
    r = client.get(
        f"{API}/team/live",
        params={"team_id": str(world["team_b"].id)},
        headers=auth(client, "admin"),
    )
    assert [c["username"] for c in r.json()["collectors"]] == ["col_b"]


def test_replay_includes_the_planned_route(
    client: TestClient,
    placed: dict,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "osrm_url", None)
    monkeypatch.setattr(get_settings(), "vroom_url", None)
    r = client.post(
        f"{API}/routes/day-plan", json={"lat": 8.24, "lon": 7.10}, headers=auth(client, "col_a")
    )
    assert r.status_code == 200
    today = datetime.now(UTC).date().isoformat()
    day = client.get(
        f"{API}/collectors/{placed['col_a'].id}/day",
        params={"date": today},
        headers=auth(client, "sup_a"),
    ).json()
    assert day["planned"]["source"] == "heuristic"
    assert len(day["planned"]["stop_order"]) == 5 and len(day["planned"]["geometry"]) >= 2


# --- Exports ----------------------------------------------------------------------
def export(client: TestClient, kind: str, user: str = "sup_a", **params: str) -> object:
    r = client.get(f"{API}/exports/{kind}", params={**RANGE, **params}, headers=auth(client, user))
    return r


def test_export_submissions_csv_and_xlsx(client: TestClient, worked: dict, db: Session) -> None:
    r = export(client, "submissions")
    assert r.status_code == 200  # type: ignore[attr-defined]
    assert "attachment" in r.headers["content-disposition"]  # type: ignore[attr-defined]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))  # type: ignore[attr-defined]
    assert len(rows) == 6  # 5 surveys + the supervisor's spot check
    surveys = [x for x in rows if x["collector"] == "Col_A"]
    assert len(surveys) == 5 and all(x["hh_size"] for x in surveys)
    assert surveys[0]["point_id"].startswith("T")

    x = export(client, "submissions", format="xlsx")
    ws = load_workbook(io.BytesIO(x.content)).active  # type: ignore[attr-defined]
    assert ws is not None and ws.max_row == 7  # header + 6
    audits = db.scalars(select(AuditLog).where(AuditLog.action == "export")).all()
    assert len(audits) == 2 and audits[0].details["rows"] == 6


def test_export_stops_flags_points_and_tracks(client: TestClient, worked: dict) -> None:
    stops = list(csv.DictReader(io.StringIO(export(client, "stops").content.decode("utf-8-sig"))))  # type: ignore[attr-defined]
    assert stops and stops[0]["collector"] == "Col_A" and int(stops[0]["dwell_s"]) > 0
    flags = list(csv.DictReader(io.StringIO(export(client, "flags").content.decode("utf-8-sig"))))  # type: ignore[attr-defined]
    assert {f["rule"] for f in flags} >= {"R1", "R6"}
    points = list(csv.DictReader(io.StringIO(export(client, "points").content.decode("utf-8-sig"))))  # type: ignore[attr-defined]
    assert len(points) == 5 and {p["status"] for p in points} == {"completed"}
    tracks = json.loads(export(client, "tracks", format="geojson").content)  # type: ignore[attr-defined]
    (f,) = tracks["features"]
    assert f["geometry"]["type"] == "LineString" and f["properties"]["collector"] == "Col_A"
    assert export(client, "tracks").status_code == 422  # type: ignore[attr-defined]
    assert export(client, "flags", format="geojson").status_code == 422  # type: ignore[attr-defined]


def test_exports_are_scoped_and_filtered(client: TestClient, worked: dict) -> None:
    # sup_b's team did nothing; it must not get col_a's data.
    assert export(client, "submissions", user="sup_b").content.decode("utf-8-sig").count("\n") == 1  # type: ignore[attr-defined]
    # Area filter: LGA B has none of col_a's work.
    r = export(client, "flags", user="admin", area_id=str(worked["lga_b"].id))
    assert r.content.decode("utf-8-sig").count("\n") == 1  # type: ignore[attr-defined]
    # sup_a can't pick another LGA's area.
    assert export(client, "flags", area_id=str(worked["lga_b"].id)).status_code == 404  # type: ignore[attr-defined]
    assert export(client, "flags", user="col_a").status_code == 403  # type: ignore[attr-defined]


def test_csv_cells_cannot_run_as_formulas(client: TestClient, placed: dict) -> None:  # noqa: F811
    now = datetime.now(UTC)
    r = client.post(
        f"{API}/sync/push",
        json={"device": {"id": str(uuid.uuid4())}, "device_time": now.isoformat(),
              "submissions": [{"id": str(uuid.uuid4()), "form_version_id": str(placed["fv"].id),
                               "assignment_id": placed["sim_points"][0].assignment_id,
                               "started_at": (now - timedelta(minutes=5)).isoformat(), "ended_at": now.isoformat(),
                               "data": {"consent": "yes", "hh_size": 2, "note": "=HYPERLINK(\"http://x\")"}}]},
        headers=auth(client, "col_a"),
    )  # fmt: skip
    assert r.json()["rejected"] == []
    today = now.date().isoformat()
    out = client.get(
        f"{API}/exports/submissions",
        params={"date_from": today, "date_to": today},
        headers=auth(client, "sup_a"),
    ).content.decode("utf-8-sig")
    assert "'=HYPERLINK" in out
