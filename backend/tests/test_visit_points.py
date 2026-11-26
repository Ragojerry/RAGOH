import io
import json

from fastapi.testclient import TestClient
from openpyxl import Workbook

from tests.conftest import auth

CSV = """external_id,name,lat,lon,type,collector,priority,household_size
HH-1,House one,8.25,7.10,household,col_a,2,5
HH-2,House two,8.30,7.70,household,col_b,,3
HH-3,Clinic,8.75,7.20,facility,,1,
"""


def upload(
    client: TestClient, who: str, campaign_id, filename: str, content: bytes, dry_run: bool = False
):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/visit-points/import",
        headers=auth(client, who),
        data={"campaign_id": str(campaign_id), "dry_run": str(dry_run).lower()},
        files={"file": (filename, content)},
    )


def test_csv_import_creates_points_with_geometry_areas_and_assignments(
    client: TestClient, world: dict
) -> None:
    r = upload(client, "admin", world["campaign"].id, "points.csv", CSV.encode())
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep == {"created": 3, "updated": 0, "assignments_created": 2, "errors": []}

    pts = client.get("/api/v1/visit-points", headers=auth(client, "admin")).json()
    by_id = {p["external_id"]: p for p in pts["items"]}
    assert pts["total"] == 3
    assert by_id["HH-1"]["lat"] == 8.25 and by_id["HH-1"]["lon"] == 7.10
    # Placed in the smallest containing area automatically (ward inside LGA A).
    assert by_id["HH-1"]["admin_area_id"] == str(world["ward_a1"].id)
    assert by_id["HH-2"]["admin_area_id"] == str(world["lga_b"].id)
    assert by_id["HH-1"]["geofence_m"] == 50 and by_id["HH-3"]["geofence_m"] == 100
    assert by_id["HH-1"]["attributes"] == {"household_size": "5"}

    mine = client.get("/api/v1/me/assignments", headers=auth(client, "col_a")).json()
    assert [a["visit_point"]["external_id"] for a in mine] == ["HH-1"]


def test_reimport_updates_instead_of_duplicating(client: TestClient, world: dict) -> None:
    upload(client, "admin", world["campaign"].id, "points.csv", CSV.encode())
    changed = CSV.replace("House one", "House one renamed")
    rep = upload(client, "admin", world["campaign"].id, "points.csv", changed.encode()).json()
    assert rep["created"] == 0 and rep["updated"] == 3 and rep["assignments_created"] == 0
    pts = client.get("/api/v1/visit-points", headers=auth(client, "admin")).json()
    assert pts["total"] == 3
    assert any(p["name"] == "House one renamed" for p in pts["items"])


def test_row_errors_are_reported_and_good_rows_still_import(
    client: TestClient, world: dict
) -> None:
    bad = (
        "external_id,name,lat,lon,type,collector\n"
        "OK-1,Fine,8.2,7.1,household,\n"
        ",No id,8.2,7.1,household,\n"
        "SWAP,Swapped,7.1,48.2,household,\n"
        "TYPE,Bad type,8.2,7.1,castle,\n"
        "WHO,Unknown collector,8.2,7.1,household,nobody\n"
        "OK-1,Duplicate,8.2,7.1,household,\n"
    )
    rep = upload(client, "admin", world["campaign"].id, "bad.csv", bad.encode()).json()
    assert rep["created"] == 1
    errors = {e["row"]: e["error"] for e in rep["errors"]}
    assert set(errors) == {3, 4, 5, 6, 7}
    assert "swapped" in errors[4]


def test_dry_run_saves_nothing(client: TestClient, world: dict) -> None:
    rep = upload(
        client, "admin", world["campaign"].id, "points.csv", CSV.encode(), dry_run=True
    ).json()
    assert rep["created"] == 3
    assert client.get("/api/v1/visit-points", headers=auth(client, "admin")).json()["total"] == 0


def test_xlsx_and_geojson_import(client: TestClient, world: dict) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["ID", "Name", "Latitude", "Longitude", "Type"])
    ws.append(["X-1", "From Excel", 8.6, 7.3, "school"])
    buf = io.BytesIO()
    wb.save(buf)
    rep = upload(client, "admin", world["campaign"].id, "pts.xlsx", buf.getvalue()).json()
    assert rep["created"] == 1 and rep["errors"] == []

    gj = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [7.8, 8.9]},
             "properties": {"external_id": "G-1", "name": "From GeoJSON", "type": "settlement"}},
        ],
    }  # fmt: skip
    rep = upload(
        client, "admin", world["campaign"].id, "pts.geojson", json.dumps(gj).encode()
    ).json()
    assert rep["created"] == 1 and rep["errors"] == []
    p = next(
        p
        for p in client.get("/api/v1/visit-points", headers=auth(client, "admin")).json()["items"]
        if p["external_id"] == "G-1"
    )
    assert p["geofence_m"] == 150 and p["admin_area_id"] == str(world["lga_b"].id)


def test_points_are_scoped_and_filterable(client: TestClient, world: dict) -> None:
    upload(client, "admin", world["campaign"].id, "points.csv", CSV.encode())
    ids = lambda who, q="": {
        p["external_id"]
        for p in client.get(f"/api/v1/visit-points{q}", headers=auth(client, who)).json()["items"]
    }  # noqa: E731
    assert ids("sup_a") == {"HH-1", "HH-3"}
    assert ids("sup_b") == {"HH-2"}
    assert ids("manager_a") == {"HH-1", "HH-3"}
    assert ids("admin", "?type=facility") == {"HH-3"}
    assert ids("admin", "?bbox=7.0,8.2,7.2,8.3") == {"HH-1"}
    assert ids("admin", f"?admin_area_id={world['lga_a'].id}") == {"HH-1", "HH-3"}
    assert client.get("/api/v1/visit-points", headers=auth(client, "col_a")).status_code == 403


def test_only_managers_and_admins_can_import(client: TestClient, world: dict) -> None:
    assert (
        upload(client, "sup_a", world["campaign"].id, "points.csv", CSV.encode()).status_code == 403
    )
    assert upload(client, "admin", world["campaign"].id, "points.txt", b"x").status_code == 422


def test_campaign_config_merges_with_defaults(client: TestClient, world: dict) -> None:
    h = auth(client, "admin")
    url = f"/api/v1/campaigns/{world['campaign'].id}/config"
    r = client.put(url, json={"stops": {"radius_m": 40}}, headers=h)
    assert r.status_code == 200
    eff = r.json()["effective"]
    assert eff["stops"]["radius_m"] == 40 and eff["stops"]["min_duration_s"] == 180
    assert client.put(url, json={}, headers=auth(client, "sup_a")).status_code == 403


def test_admin_area_geojson_import(client: TestClient, world: dict) -> None:
    gj = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"code": "WARD-B1", "name": "Ward B1", "level": "ward", "parent_code": "LGA-B"},
             "geometry": {"type": "Polygon", "coordinates": [[[7.5, 8.0], [7.75, 8.0], [7.75, 8.5], [7.5, 8.5], [7.5, 8.0]]]}},
            {"type": "Feature", "properties": {"code": "BAD", "name": "No parent", "level": "ward", "parent_code": "NOPE"},
             "geometry": None},
        ],
    }  # fmt: skip
    r = client.post(
        "/api/v1/admin-areas/import",
        headers=auth(client, "admin"),
        files={"file": ("areas.geojson", json.dumps(gj).encode())},
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1 and len(r.json()["errors"]) == 1
    codes = {
        a["code"] for a in client.get("/api/v1/admin-areas", headers=auth(client, "sup_b")).json()
    }
    assert codes == {"LGA-B", "WARD-B1"}
