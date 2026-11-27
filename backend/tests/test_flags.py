"""Phase 5: abnormal-behaviour flags, risk score and supervisor follow-up, end to end."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.forms import publish_form
from app.db.models import (
    Assignment,
    AssignmentKind,
    Backcheck,
    CollectorDaySummary,
    Flag,
    FlagStatus,
    FormKind,
)
from app.seed_forms import backcheck, supervision_checklist
from app.services.flags import evaluate_day, evaluate_supervision
from app.simulator import simulate
from tests.conftest import auth
from tests.test_tracking import DAY, START, placed  # noqa: F401 — fixture

LOC = lambda la, lo: {"lat": la, "lon": lo, "accuracy_m": 6}  # noqa: E731


def push_day(
    client: TestClient,
    placed: dict,
    scenario: str,
    *,
    device_offset_s: int = 0,
    start: datetime = START,
) -> None:  # noqa: F811
    """Push a simulated day including 'completed' status changes, like the phone does."""
    day = simulate(scenario, placed["sim_points"], start)
    shift_id = str(uuid.uuid4())
    body = {
        "device": {"id": str(uuid.uuid4())},
        "device_time": (datetime.now(UTC) + timedelta(seconds=device_offset_s)).isoformat(),
        "shifts": [{"id": shift_id, "started_at": day.shift_start.isoformat(), "ended_at": day.shift_end.isoformat()}],  # type: ignore[union-attr]
        "gps_points": [
            {"id": str(uuid.uuid4()), "recorded_at": f.t.isoformat(), "lat": f.lat, "lon": f.lon, "accuracy_m": f.accuracy_m, "is_mock": f.is_mock}
            for f in day.fixes
        ],
        "submissions": [
            {"id": str(uuid.uuid4()), "form_version_id": str(placed["fv"].id), "shift_id": shift_id, "assignment_id": s.assignment_id,
             "started_at": s.started_at.isoformat(), "ended_at": s.ended_at.isoformat(), "start_location": LOC(s.lat, s.lon),
             "end_location": LOC(s.lat, s.lon), "data": {"consent": "yes", "hh_size": 3 + i % 4, "under5": i % 2, "has_net": "yes" if i % 2 else "no"}}
            for i, s in enumerate(day.submissions)
        ],
        "assignment_updates": [
            {"id": str(uuid.uuid4()), "assignment_id": s.assignment_id, "status": "completed", "changed_at": s.ended_at.isoformat()}
            for s in day.submissions
        ],
    }  # fmt: skip
    r = client.post("/api/v1/sync/push", json=body, headers=auth(client, "col_a"))
    assert r.status_code == 200, r.text
    assert r.json()["rejected"] == []


def rules(db: Session, user_id) -> dict[str, int]:  # type: ignore[no-untyped-def]
    out: dict[str, int] = {}
    for f in db.scalars(select(Flag).where(Flag.collector_id == user_id)):
        out[f.rule_id] = out.get(f.rule_id, 0) + 1
    return out


def test_cheater_triggers_the_expected_flags(client: TestClient, placed: dict, db: Session) -> None:  # noqa: F811
    """Phase 5 'done when' (part 1): the cheating collector is caught."""
    push_day(client, placed, "cheater")
    found = rules(db, placed["col_a"].id)
    assert found.get("R1") == 5  # every survey filled far from its house
    assert found.get("R6") == 5  # every point marked done without being visited
    assert found.get("R2", 0) >= 3  # 2–3 minute interviews
    assert found.get("R9") == 1  # hours idle at home during the shift
    summary = db.get(CollectorDaySummary, (placed["col_a"].id, DAY))
    assert summary is not None and summary.risk_score >= 80
    assert summary.risk_breakdown["R1"] == 5


def test_honest_day_raises_nothing_serious(client: TestClient, placed: dict, db: Session) -> None:  # noqa: F811
    push_day(client, placed, "honest")
    flags = list(db.scalars(select(Flag).where(Flag.collector_id == placed["col_a"].id)))
    assert [f for f in flags if f.severity.value != "low"] == [], [
        (f.rule_id, f.details) for f in flags
    ]


def test_spoofer_and_gaps(client: TestClient, placed: dict, db: Session) -> None:  # noqa: F811
    push_day(client, placed, "spoofer")
    assert rules(db, placed["col_a"].id).get("R8") == 1


def test_phone_off_gap_is_flagged(client: TestClient, placed: dict, db: Session) -> None:  # noqa: F811
    push_day(client, placed, "gaps")
    assert rules(db, placed["col_a"].id).get("R10") == 1


def test_wrong_phone_clock_is_flagged(client: TestClient, placed: dict, db: Session) -> None:  # noqa: F811
    push_day(client, placed, "honest", device_offset_s=2 * 3600)
    assert rules(db, placed["col_a"].id).get("R13") == 1


def test_burst_of_surveys_and_duplicates(client: TestClient, placed: dict, db: Session) -> None:  # noqa: F811
    t0 = START + timedelta(hours=2)
    a0 = placed["sim_points"][0].assignment_id
    subs = []
    for i in range(6):
        st = t0 + timedelta(minutes=2 * i)
        subs.append(
            {"id": str(uuid.uuid4()), "form_version_id": str(placed["fv"].id), "assignment_id": a0 if i < 2 else placed["sim_points"][i % 5].assignment_id,
             "started_at": st.isoformat(), "ended_at": (st + timedelta(minutes=4)).isoformat(), "data": {"consent": "yes", "hh_size": 4, "under5": 1, "has_net": "no"}}
        )  # fmt: skip
    body = {
        "device": {"id": str(uuid.uuid4())},
        "device_time": datetime.now(UTC).isoformat(),
        "submissions": subs,
    }
    assert (
        client.post("/api/v1/sync/push", json=body, headers=auth(client, "col_a")).status_code
        == 200
    )
    flags = list(db.scalars(select(Flag).where(Flag.collector_id == placed["col_a"].id)))
    titles = {f.title for f in flags}
    assert "Too many surveys in a short time" in titles
    assert "Surveys overlap in time" in titles
    assert "Duplicate surveys for the same point" in titles
    assert "Many surveys with identical answers" in titles


def test_re_evaluation_keeps_acted_flags_and_drops_stale_ones(
    client: TestClient, placed: dict, db: Session
) -> None:  # noqa: F811
    push_day(client, placed, "cheater")
    flags = list(
        db.scalars(
            select(Flag).where(Flag.collector_id == placed["col_a"].id, Flag.rule_id == "R1")
        )
    )
    flags[0].status = FlagStatus.acknowledged
    db.flush()
    cfg_off = {"flags": {"R1": {"enabled": False}}}
    placed["campaign"].config = cfg_off
    db.flush()
    evaluate_day(db, placed["col_a"].id, DAY)
    remaining = list(
        db.scalars(
            select(Flag).where(Flag.collector_id == placed["col_a"].id, Flag.rule_id == "R1")
        )
    )
    assert [f.id for f in remaining] == [flags[0].id]  # the acknowledged one survives


@pytest.fixture
def cheated(client: TestClient, placed: dict, db: Session) -> dict:  # noqa: F811
    publish_form(db, placed["campaign"], FormKind.backcheck, backcheck(), "b.xlsx", None)
    publish_form(
        db, placed["campaign"], FormKind.supervision, supervision_checklist(), "c.xlsx", None
    )
    push_day(client, placed, "cheater")
    return placed


def test_flags_inbox_is_scoped(client: TestClient, cheated: dict) -> None:
    r = client.get("/api/v1/flags", headers=auth(client, "sup_a"))
    assert r.status_code == 200 and r.json()["total"] >= 12
    assert r.json()["items"][0]["severity"] == "high"  # most serious first
    assert client.get("/api/v1/flags", headers=auth(client, "sup_b")).json()["total"] == 0
    assert (
        client.get("/api/v1/flags", headers=auth(client, "col_a")).json()["total"] == 0
    )  # nothing asked yet
    only_r6 = client.get(
        "/api/v1/flags",
        params={"rule_id": "R6", "severity": "high"},
        headers=auth(client, "manager_a"),
    ).json()
    assert only_r6["total"] == 5


def test_supervisor_follow_up_end_to_end(client: TestClient, cheated: dict, db: Session) -> None:
    """Phase 5 'done when' (part 2): a supervisor follows up and resolves the flags."""
    sup = auth(client, "sup_a")
    flag = client.get("/api/v1/flags", params={"rule_id": "R6"}, headers=sup).json()["items"][0]
    url = f"/api/v1/flags/{flag['id']}/actions"

    assert (
        client.post(url, json={"action": "acknowledge"}, headers=sup).json()["status"]
        == "acknowledged"
    )
    assert (
        client.post(url, json={"action": "call", "phone": "08030000000"}, headers=sup).status_code
        == 200
    )

    # Ask the collector to explain → the phone receives the flag and the message.
    r = client.post(
        url,
        json={"action": "request_explanation", "comment": "Did you visit this house?"},
        headers=sup,
    )
    assert r.json()["status"] == "under_review"
    pull = client.get("/api/v1/sync/pull", headers=auth(client, "col_a")).json()
    assert [f["id"] for f in pull["my_flags"]] == [flag["id"]]
    assert pull["messages"][0]["body"] == "Did you visit this house?"
    assert pull["supervisors"][0]["full_name"] == "Sup_A"

    # The collector replies from the phone (offline-capable, idempotent).
    reply = {
        "id": str(uuid.uuid4()),
        "flag_id": flag["id"],
        "comment": "The family moved; I asked a neighbour.",
        "sent_at": datetime.now(UTC).isoformat(),
    }
    body = {
        "device": {"id": str(uuid.uuid4())},
        "device_time": datetime.now(UTC).isoformat(),
        "flag_replies": [reply],
    }
    for _ in range(2):
        assert client.post("/api/v1/sync/push", json=body, headers=auth(client, "col_a")).json()[
            "accepted"
        ]["flag_replies"] == [reply["id"]]
    detail = client.get(f"/api/v1/flags/{flag['id']}", headers=sup).json()
    assert [a["action"] for a in detail["actions"]] == [
        "acknowledge",
        "call",
        "request_explanation",
        "reply",
    ]
    assert any(
        m["body"].startswith("The family moved")
        for m in client.get("/api/v1/messages", headers=sup).json()
    )

    # Back-check by the supervisor (someone other than the collector).
    r = client.post(
        url, json={"action": "backcheck", "comment": "Verify household size"}, headers=sup
    )
    assert r.status_code == 200, r.text
    bc = db.scalar(select(Backcheck))
    assert bc is not None and bc.assigned_to == cheated["sup_a"].id
    sup_pull = client.get("/api/v1/sync/pull", headers=sup).json()
    task = next(a for a in sup_pull["assignments"] if a["kind"] == "backcheck")
    bc_form = next(f for f in sup_pull["forms"] if f["kind"] == "backcheck")
    now = datetime.now(UTC)
    sub = {"id": str(uuid.uuid4()), "form_version_id": bc_form["version_id"], "assignment_id": task["id"],
           "started_at": (now - timedelta(minutes=6)).isoformat(), "ended_at": now.isoformat(),
           "data": {"visited": "no", "hh_size": 9, "has_net": "no"}}  # fmt: skip
    push = {
        "device": {"id": str(uuid.uuid4())},
        "device_time": now.isoformat(),
        "submissions": [sub],
    }
    assert client.post("/api/v1/sync/push", json=push, headers=sup).json()["rejected"] == []
    db.refresh(bc)
    assert bc.status.value == "completed"
    assert bc.match["compared"] >= 1 and bc.match["differences"]
    assert "backcheck_completed" in [
        a["action"]
        for a in client.get(f"/api/v1/flags/{flag['id']}", headers=sup).json()["actions"]
    ]

    # Resolve needs a comment; closed flags refuse further actions until reopened.
    assert client.post(url, json={"action": "resolve"}, headers=sup).status_code == 422
    r = client.post(
        url,
        json={"action": "resolve", "comment": "Back-check confirms no visit; collector warned."},
        headers=sup,
    )
    assert r.json()["status"] == "resolved"
    assert client.post(url, json={"action": "escalate"}, headers=sup).status_code == 409
    assert (
        client.post(url, json={"action": "reopen"}, headers=sup).json()["status"] == "acknowledged"
    )


def test_dismissing_lowers_the_risk_score(client: TestClient, cheated: dict, db: Session) -> None:
    sup = auth(client, "sup_a")
    before = db.get(CollectorDaySummary, (cheated["col_a"].id, DAY)).risk_score  # type: ignore[union-attr]
    for f in client.get("/api/v1/flags", params={"rule_id": "R1"}, headers=sup).json()["items"]:
        client.post(
            f"/api/v1/flags/{f['id']}/actions",
            json={"action": "dismiss", "comment": "GPS was poor in that area"},
            headers=sup,
        )
    for f in client.get("/api/v1/flags", params={"rule_id": "R6"}, headers=sup).json()["items"]:
        client.post(
            f"/api/v1/flags/{f['id']}/actions",
            json={"action": "dismiss", "comment": "GPS was poor in that area"},
            headers=sup,
        )
    db.expire_all()
    after = db.get(CollectorDaySummary, (cheated["col_a"].id, DAY)).risk_score  # type: ignore[union-attr]
    assert after < before


def test_risk_list_and_permissions(client: TestClient, cheated: dict) -> None:
    rows = client.get(
        "/api/v1/risk", params={"date": DAY.isoformat()}, headers=auth(client, "sup_a")
    ).json()
    assert [r["username"] for r in rows] == ["col_a"]
    assert rows[0]["risk_score"] >= 80 and rows[0]["open_flags"] >= 12
    assert (
        client.get(
            "/api/v1/risk", params={"date": DAY.isoformat()}, headers=auth(client, "col_a")
        ).status_code
        == 403
    )
    flag = client.get("/api/v1/flags", headers=auth(client, "sup_a")).json()["items"][0]
    # Collectors can't act as supervisors; supervisors of other teams can't see the flag.
    assert (
        client.post(
            f"/api/v1/flags/{flag['id']}/actions",
            json={"action": "resolve", "comment": "x"},
            headers=auth(client, "col_a"),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/flags/{flag['id']}/actions",
            json={"action": "acknowledge"},
            headers=auth(client, "sup_b"),
        ).status_code
        == 404
    )


def test_backcheck_cannot_be_done_by_the_checked_collector(
    client: TestClient, cheated: dict
) -> None:
    flag = client.get(
        "/api/v1/flags", params={"rule_id": "R6"}, headers=auth(client, "sup_a")
    ).json()["items"][0]
    r = client.post(
        f"/api/v1/flags/{flag['id']}/actions",
        json={"action": "backcheck", "assignee_id": str(cheated["col_a"].id)},
        headers=auth(client, "sup_a"),
    )
    assert r.status_code == 422
    r = client.post(
        f"/api/v1/flags/{flag['id']}/actions",
        json={"action": "backcheck", "assignee_id": str(cheated["col_b"].id)},
        headers=auth(client, "sup_a"),
    )
    assert r.status_code == 422  # other team


def test_revisit_sends_the_point_back(client: TestClient, cheated: dict, db: Session) -> None:
    flag = client.get(
        "/api/v1/flags", params={"rule_id": "R6"}, headers=auth(client, "sup_a")
    ).json()["items"][0]
    r = client.post(
        f"/api/v1/flags/{flag['id']}/actions",
        json={"action": "revisit", "comment": "Go back and interview the household"},
        headers=auth(client, "sup_a"),
    )
    assert r.status_code == 200
    a = db.get(Assignment, uuid.UUID(flag["subject_id"]))
    assert a is not None and a.status.value == "pending" and a.kind == AssignmentKind.visit
    assert a.note == "Go back and interview the household"


def test_messages(client: TestClient, cheated: dict) -> None:
    col = auth(client, "col_a")
    ok = client.post(
        "/api/v1/messages",
        json={"recipient_id": str(cheated["sup_a"].id), "body": "My phone battery is low"},
        headers=col,
    )
    assert ok.status_code == 201
    assert (
        client.post(
            "/api/v1/messages",
            json={"recipient_id": str(cheated["sup_b"].id), "body": "hi"},
            headers=col,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/messages",
            json={"team_id": str(cheated["team_a"].id), "body": "hi"},
            headers=col,
        ).status_code
        == 403
    )
    sup = auth(client, "sup_a")
    assert (
        client.post(
            "/api/v1/messages",
            json={"team_id": str(cheated["team_a"].id), "body": "Meet at 8"},
            headers=sup,
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/messages",
            json={"team_id": str(cheated["team_b"].id), "body": "x"},
            headers=sup,
        ).status_code
        == 403
    )
    assert "Meet at 8" in [
        m["body"] for m in client.get("/api/v1/sync/pull", headers=col).json()["messages"]
    ]


def test_supervision_rules(client: TestClient, cheated: dict, db: Session) -> None:
    # S1: col_a worked but has no supervision checklist; S2: high flags left 'new' for over 24 h.
    for f in db.scalars(select(Flag).where(Flag.collector_id == cheated["col_a"].id)):
        f.created_at = datetime.now(UTC) - timedelta(hours=30)
    db.flush()
    evaluate_supervision(db, DAY + timedelta(days=1))
    found = rules(db, cheated["col_a"].id)
    assert found.get("S1") == 1
    assert found.get("S2", 0) >= 10

    # A supervision checklist for col_a clears S1 on the next run.
    sup_pull = client.get("/api/v1/sync/pull", headers=auth(client, "sup_a")).json()
    form = next(f for f in sup_pull["forms"] if f["kind"] == "supervision")
    now = datetime.now(UTC)
    sub = {"id": str(uuid.uuid4()), "form_version_id": form["version_id"], "started_at": (now - timedelta(minutes=10)).isoformat(),
           "ended_at": now.isoformat(), "data": {"collector_username": "col_a", "on_time": "yes"}}  # fmt: skip
    client.post(
        "/api/v1/sync/push",
        json={
            "device": {"id": str(uuid.uuid4())},
            "device_time": now.isoformat(),
            "submissions": [sub],
        },
        headers=auth(client, "sup_a"),
    )
    evaluate_supervision(db, DAY + timedelta(days=1))
    assert rules(db, cheated["col_a"].id).get("S1") is None


def test_nightly_job_runs(client: TestClient, cheated: dict, db: Session) -> None:
    from app.maintenance import nightly_flags

    db.commit = db.flush  # type: ignore[method-assign]  # keep the test transaction
    out = nightly_flags(db, today=DAY + timedelta(days=1))
    assert out["collectors"] >= 2
    assert rules(db, cheated["col_a"].id).get("R1") == 5
