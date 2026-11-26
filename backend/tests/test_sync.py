import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.forms import publish_form
from app.db.models import Assignment, FormKind, PointType, Shift, Submission, VisitPoint
from app.seed_forms import backcheck, household_survey, supervision_checklist
from app.services.geo import point_wkt
from tests.conftest import auth


@pytest.fixture
def field(db: Session, world: dict) -> dict:  # type: ignore[type-arg]
    """world + published forms + two assigned points for col_a."""
    _, survey_v, _ = publish_form(
        db, world["campaign"], FormKind.survey, household_survey(), "s.xlsx", None
    )
    _, sup_v, _ = publish_form(
        db, world["campaign"], FormKind.supervision, supervision_checklist(), "c.xlsx", None
    )
    publish_form(db, world["campaign"], FormKind.backcheck, backcheck(), "b.xlsx", None)
    assignments = []
    for i in range(2):
        p = VisitPoint(
            campaign_id=world["campaign"].id, external_id=f"P{i}", name=f"Point {i}", type=PointType.household,
            admin_area_id=world["lga_a"].id, geom=point_wkt(7.1 + i / 100, 8.2), geofence_m=50,
        )  # fmt: skip
        db.add(p)
        db.flush()
        a = Assignment(visit_point_id=p.id, collector_id=world["col_a"].id)
        db.add(a)
        db.flush()
        assignments.append(a)
    return {**world, "survey_v": survey_v, "sup_v": sup_v, "assignments": assignments}


def now() -> datetime:
    return datetime.now(UTC)


def push_body(field: dict, **overrides) -> dict:  # type: ignore[type-arg, no-untyped-def]
    start = now() - timedelta(minutes=12)
    shift_id = str(uuid.uuid4())
    body = {
        "device": {"id": str(uuid.uuid4()), "model": "Tecno Spark 10", "android_version": "13", "app_version": "0.2.0"},
        "device_time": now().isoformat(),
        "shifts": [{"id": shift_id, "started_at": (start - timedelta(hours=1)).isoformat(),
                    "start_location": {"lat": 8.2, "lon": 7.1, "accuracy_m": 8}}],
        "submissions": [{
            "id": str(uuid.uuid4()),
            "form_version_id": str(field["survey_v"].id),
            "shift_id": shift_id,
            "assignment_id": str(field["assignments"][0].id),
            "started_at": start.isoformat(),
            "ended_at": (start + timedelta(minutes=9)).isoformat(),
            "start_location": {"lat": 8.2, "lon": 7.1, "accuracy_m": 6},
            "end_location": {"lat": 8.2001, "lon": 7.1001, "accuracy_m": 5},
            "data": {"consent": "yes", "respondent_name": "Amina", "hh_size": 5, "under5": 1, "photo": "photo.jpg"},
        }],
        "assignment_updates": [{"id": str(uuid.uuid4()), "assignment_id": str(field["assignments"][0].id),
                                "status": "completed", "changed_at": now().isoformat()}],
    }  # fmt: skip
    body.update(overrides)
    return body


def test_pull_gives_collector_everything_needed_offline(client: TestClient, field: dict) -> None:
    r = client.get("/api/v1/sync/pull", headers=auth(client, "col_a"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["user"]["username"] == "col_a"
    assert [c["name"] for c in data["campaigns"]] == ["Test Campaign"]
    assert data["campaigns"][0]["config"]["stops"]["radius_m"] == 50
    kinds = {f["kind"] for f in data["forms"]}
    assert kinds == {"survey", "backcheck"}  # collectors don't get supervisor checklists
    survey = next(f for f in data["forms"] if f["kind"] == "survey")
    assert survey["schema"]["id_string"] == "ragoh_household_survey"
    assert len(data["assignments"]) == 2
    assert data["assignments"][0]["visit_point"]["lat"] == 8.2


def test_supervisor_pull_includes_supervision_forms(client: TestClient, field: dict) -> None:
    data = client.get("/api/v1/sync/pull", headers=auth(client, "sup_a")).json()
    assert {f["kind"] for f in data["forms"]} == {"survey", "supervision", "backcheck"}
    assert data["assignments"] == []


def test_push_stores_records_and_metadata(client: TestClient, field: dict, db: Session) -> None:
    body = push_body(field)
    r = client.post("/api/v1/sync/push", json=body, headers=auth(client, "col_a"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["rejected"] == []
    assert len(out["accepted"]["submissions"]) == 1
    sub = db.get(Submission, uuid.UUID(body["submissions"][0]["id"]))
    assert sub is not None
    assert sub.duration_s == 540
    assert sub.visit_point_id == field["assignments"][0].visit_point_id
    assert sub.accuracy_m == 5
    assert abs(sub.clock_offset_s or 0) < 5
    db.refresh(field["assignments"][0])
    assert field["assignments"][0].status.value == "completed"


def test_repeated_push_never_duplicates(client: TestClient, field: dict, db: Session) -> None:
    """Phase 2 'done when': a survey pushed again after a dropped connection is stored once."""
    body = push_body(field)
    h = auth(client, "col_a")
    for _ in range(3):
        r = client.post("/api/v1/sync/push", json=body, headers=h)
        assert r.status_code == 200
        assert r.json()["accepted"]["submissions"] == [body["submissions"][0]["id"]]
    assert db.scalar(select(func.count()).select_from(Submission)) == 1
    assert db.scalar(select(func.count()).select_from(Shift)) == 1


def test_shift_end_is_recorded_on_a_later_push(
    client: TestClient, field: dict, db: Session
) -> None:
    body = push_body(field, submissions=[], assignment_updates=[])
    h = auth(client, "col_a")
    client.post("/api/v1/sync/push", json=body, headers=h)
    body["shifts"][0]["ended_at"] = now().isoformat()
    body["shifts"][0]["end_location"] = {"lat": 8.21, "lon": 7.11}
    client.post("/api/v1/sync/push", json=body, headers=h)
    shift = db.get(Shift, uuid.UUID(body["shifts"][0]["id"]))
    assert shift is not None and shift.ended_at is not None and shift.end_reason == "manual"


def test_push_rejects_records_outside_the_users_scope(
    client: TestClient, field: dict, world: dict
) -> None:
    body = push_body(field)
    # col_b cannot submit against col_a's assignment or update its status
    r = client.post("/api/v1/sync/push", json=body, headers=auth(client, "col_b"))
    errors = {i["kind"]: i["error"] for i in r.json()["rejected"]}
    assert errors["submission"] in ("assignment is not yours", "form is not in your campaigns")
    assert errors["assignment_update"] == "assignment is not yours"

    bad = push_body(field, shifts=[], assignment_updates=[])
    bad["submissions"][0]["form_version_id"] = str(uuid.uuid4())
    r = client.post("/api/v1/sync/push", json=bad, headers=auth(client, "col_a"))
    assert r.json()["rejected"][0]["error"] == "unknown form version"


def test_assignment_status_last_change_wins(client: TestClient, field: dict, db: Session) -> None:
    a = str(field["assignments"][1].id)
    t = now()
    updates = [
        {"id": str(uuid.uuid4()), "assignment_id": a, "status": "refused", "reason": "no adult home",
         "changed_at": (t - timedelta(minutes=5)).isoformat()},
        {"id": str(uuid.uuid4()), "assignment_id": a, "status": "completed", "changed_at": t.isoformat()},
    ]  # fmt: skip
    h = auth(client, "col_a")
    client.post(
        "/api/v1/sync/push",
        json=push_body(field, shifts=[], submissions=[], assignment_updates=updates[::-1]),
        headers=h,
    )
    db.refresh(field["assignments"][1])
    assert field["assignments"][1].status.value == "completed"
    # An older change arriving later does not overwrite a newer one.
    client.post(
        "/api/v1/sync/push",
        json=push_body(field, shifts=[], submissions=[], assignment_updates=[updates[0]]),
        headers=h,
    )
    db.refresh(field["assignments"][1])
    assert field["assignments"][1].status.value == "completed"


def test_blocked_device_is_refused(client: TestClient, field: dict, db: Session) -> None:
    from app.db.models import Device

    body = push_body(field)
    db.add(Device(id=uuid.UUID(body["device"]["id"]), is_blocked=True))
    db.flush()
    assert (
        client.post("/api/v1/sync/push", json=body, headers=auth(client, "col_a")).status_code
        == 423
    )


def test_media_upload_after_submission(
    client: TestClient, field: dict, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path))
    body = push_body(field)
    h = auth(client, "col_a")
    sid = body["submissions"][0]["id"]
    url = f"/api/v1/submissions/{sid}/media"
    jpg = b"\xff\xd8\xff\xe0" + b"0" * 1000
    assert (
        client.post(
            url,
            data={"field_name": "photo"},
            files={"file": ("p.jpg", jpg, "image/jpeg")},
            headers=h,
        ).status_code
        == 404
    )
    client.post("/api/v1/sync/push", json=body, headers=h)
    r = client.post(
        url, data={"field_name": "photo"}, files={"file": ("p.jpg", jpg, "image/jpeg")}, headers=h
    )
    assert r.status_code == 200, r.text
    assert (tmp_path / sid / "photo.jpg").read_bytes() == jpg
    # Re-upload replaces, wrong type refused, unknown field refused
    assert (
        client.post(
            url,
            data={"field_name": "photo"},
            files={"file": ("p.jpg", jpg, "image/jpeg")},
            headers=h,
        ).status_code
        == 200
    )
    assert (
        client.post(
            url,
            data={"field_name": "photo"},
            files={"file": ("p.gif", b"GIF8", "image/gif")},
            headers=h,
        ).status_code
        == 415
    )
    assert (
        client.post(
            url,
            data={"field_name": "nope"},
            files={"file": ("p.jpg", jpg, "image/jpeg")},
            headers=h,
        ).status_code
        == 422
    )


def test_form_upload_versions_and_errors(client: TestClient, world: dict) -> None:
    h = auth(client, "admin")
    cid = str(world["campaign"].id)

    def up(content: bytes, kind: str = "survey"):  # type: ignore[no-untyped-def]
        return client.post("/api/v1/forms", headers=h, data={"campaign_id": cid, "kind": kind},
                           files={"file": ("f.xlsx", content)})  # fmt: skip

    r = up(household_survey())
    assert r.status_code == 201, r.text
    assert r.json()["schema"]["fields"][1]["name"] == "consent"
    assert up(household_survey()).status_code == 200  # same version + content: no-op

    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(household_survey()))
    wb["survey"].append(["text", "extra", "Extra question"])
    buf = io.BytesIO()
    wb.save(buf)
    r = up(buf.getvalue())
    assert r.status_code == 422 and "increase the version" in r.json()["detail"]
    wb["settings"]["C2"] = "2026112602"
    buf = io.BytesIO()
    wb.save(buf)
    assert up(buf.getvalue()).status_code == 201

    assert up(household_survey(), kind="supervision").status_code == 422
    assert up(b"not an xlsx").status_code == 422
    assert client.post("/api/v1/forms", headers=auth(client, "sup_a"), data={"campaign_id": cid},
                       files={"file": ("f.xlsx", household_survey())}).status_code == 403  # fmt: skip

    forms = client.get("/api/v1/forms", headers=h).json()
    assert [(f["id_string"], f["version"]) for f in forms] == [
        ("ragoh_household_survey", "2026112602")
    ]
    xls = client.get(f"/api/v1/forms/versions/{forms[0]['version_id']}/xlsform", headers=h)
    assert xls.status_code == 200 and xls.content[:2] == b"PK"


def test_unsupported_xlsform_feature_is_rejected(client: TestClient, world: dict) -> None:
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(backcheck()))
    wb["survey"].append(["audio", "voice", "Record a message"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/v1/forms", headers=auth(client, "admin"), data={"campaign_id": str(world["campaign"].id)},
                    files={"file": ("f.xlsx", buf.getvalue())})  # fmt: skip
    assert r.status_code == 422
    assert "audio" in r.json()["detail"]
