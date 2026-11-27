"""Phase 7b: Elasticsearch, DHIS2 and DIGIT HCM connectors (against fake servers)."""

import hashlib
import hmac
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import IntegrationState
from app.integrations import base
from app.integrations.base import run_all
from tests.conftest import auth
from tests.test_flags import push_day
from tests.test_tracking import START, placed  # noqa: F401 — fixture

API = "/api/v1"
NOW = START + timedelta(hours=10)


class FakeServer:
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.respond = respond

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.respond:
            return self.respond(req)
        return httpx.Response(200, json={"errors": False, "items": []})

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]


@pytest.fixture
def fake() -> Iterator[Callable[..., FakeServer]]:
    def install(respond: Callable[[httpx.Request], httpx.Response] | None = None) -> FakeServer:
        server = FakeServer(respond)
        base.transport = httpx.MockTransport(server)
        return server

    yield install
    base.transport = None


def enable(db: Session, placed: dict, name: str, **cfg: Any) -> None:  # noqa: F811
    camp = placed["campaign"]
    camp.config = {**(camp.config or {}), "integrations": {name: {"enabled": True, **cfg}}}
    db.flush()


def state(db: Session, placed: dict, name: str) -> IntegrationState:  # noqa: F811
    st = db.get(IntegrationState, (name, placed["campaign"].id))
    assert st is not None
    db.refresh(st)
    return st


# --- Elasticsearch -------------------------------------------------------------------
def bulk_docs(server: FakeServer, index_suffix: str) -> list[tuple[dict, dict]]:
    out = []
    for r in server.requests:
        if r.url.path != "/_bulk":
            continue
        lines = [json.loads(x) for x in r.content.decode().strip().split("\n")]
        for action, doc in zip(lines[::2], lines[1::2], strict=True):
            if action["index"]["_index"].endswith(index_suffix):
                out.append((action, doc))
    return out


def test_elasticsearch_indexes_everything_once(
    client: TestClient,
    placed: dict,  # noqa: F811
    db: Session,
    fake: Callable[..., FakeServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "es_api_key", "aWQ6a2V5")
    push_day(client, placed, "cheater")
    enable(db, placed, "elasticsearch", url="https://es.test:9200")
    es = fake()
    (rep,) = run_all(db, now=NOW)
    assert rep["status"] == "ok", rep
    assert (
        rep["submissions"] == 5
        and rep["day_summaries"] == 1
        and rep["stops"] >= 1
        and rep["flags"] >= 10
    )

    templates = [r for r in es.requests if r.method == "PUT"]
    assert {r.url.path for r in templates} == {
        f"/_index_template/ragoh-{k}" for k in ("submissions", "stops", "day-summaries", "flags")
    }
    mapping = json.loads(templates[0].content)["template"]["mappings"]["properties"]
    assert mapping["location"]["type"] == "geo_point" or "location" not in mapping
    assert all(r.headers["authorization"] == "ApiKey aWQ6a2V5" for r in es.requests)

    subs = bulk_docs(es, "-submissions")
    action, doc = subs[0]
    assert action["index"]["_id"]  # RAGOH id: re-sending updates, never duplicates
    assert doc["collector"] == "Col_A" and doc["form_kind"] == "survey" and "lat" in doc["location"]
    assert doc["answers"]["hh_size"] and doc["point_id"].startswith("T")
    assert any(
        r.url.path == "/ragoh-stops/_delete_by_query" for r in es.requests
    )  # day's stops replaced

    # Next run: templates not resent; records from the 5-minute look-back are sent again
    # under the same ids, so Elasticsearch updates them instead of duplicating.
    first_ids = {a["index"]["_id"] for a, _ in subs}
    es.requests.clear()
    run_all(db, now=NOW + timedelta(minutes=10))
    assert not [r for r in es.requests if r.method == "PUT"]
    assert {a["index"]["_id"] for a, _ in bulk_docs(es, "-submissions")} <= first_ids


def test_elasticsearch_pseudonymous_labels_and_errors_are_recorded(
    client: TestClient,
    placed: dict,  # noqa: F811
    db: Session,
    fake: Callable[..., FakeServer],
) -> None:
    push_day(client, placed, "honest")
    enable(db, placed, "elasticsearch", url="https://es.test", collector_label="id")
    es = fake(
        lambda r: (
            httpx.Response(
                200,
                json={
                    "errors": True,
                    "items": [{"index": {"error": {"type": "mapper_parsing_exception"}}}],
                },
            )
            if r.url.path == "/_bulk"
            else httpx.Response(200, json={})
        )
    )
    (rep,) = run_all(db, now=NOW)
    assert rep["status"] == "error"
    st = state(db, placed, "elasticsearch")
    assert st.last_status == "error" and "mapper_parsing_exception" in (st.last_error or "")
    assert st.cursor == {}  # nothing marked as sent
    sent = json.loads(es.requests[-1].content.decode().split("\n")[1])
    assert sent["collector"] == str(placed["col_a"].id)  # ids only, no names


def test_connectors_respect_their_schedule_and_config(
    placed: dict, db: Session, fake: Callable[..., FakeServer]
) -> None:  # noqa: F811
    enable(db, placed, "elasticsearch")  # no url
    fake()
    (rep,) = run_all(db, now=NOW)
    assert rep["status"] == "error" and "url" in rep["error"]
    assert run_all(db, now=NOW + timedelta(minutes=1)) == []  # every_min not reached
    assert len(run_all(db, now=NOW + timedelta(minutes=1), force=True)) == 1


# --- DHIS2 -----------------------------------------------------------------------------
def dhis2_ok(req: httpx.Request) -> httpx.Response:
    n = len(json.loads(req.content)["dataValues"])
    return httpx.Response(
        200,
        json={
            "httpStatus": "OK",
            "response": {
                "status": "SUCCESS",
                "importCount": {"imported": n, "updated": 0, "ignored": 0},
                "conflicts": [],
            },
        },
    )


def test_dhis2_sends_daily_indicators_per_org_unit(
    client: TestClient,
    placed: dict,  # noqa: F811
    db: Session,
    fake: Callable[..., FakeServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "dhis2_password", "district")
    push_day(client, placed, "cheater")
    enable(
        db, placed, "dhis2", url="https://dhis2.test", username="ragoh", days_back=2, data_set="dsRAGOH01",
        org_units={"LGA-A": "ouLGAA00001", "LGA-B": "ouLGAB00001"},
        data_elements={"visits_completed": "deVisits001", "surveys": "deSurvey001", "coverage_pct": "deCover0001",
                       "flags_high": "deFlagHi001", "points_total": "dePoints001"},
    )  # fmt: skip
    server = fake(dhis2_ok)
    (rep,) = run_all(db, now=NOW)
    assert rep["status"] == "ok", rep
    (req,) = server.requests
    assert req.url.path == "/api/dataValueSets"
    assert (
        req.url.params["importStrategy"] == "CREATE_AND_UPDATE"
        and req.url.params["dryRun"] == "false"
    )
    assert req.headers["authorization"].startswith("Basic ")
    body = json.loads(req.content)
    assert body["dataSet"] == "dsRAGOH01"
    today = START.astimezone().strftime("%Y%m%d")
    v = {
        (x["orgUnit"], x["dataElement"]): x["value"]
        for x in body["dataValues"]
        if x["period"] == today
    }
    assert v[("ouLGAA00001", "deVisits001")] == "5" and v[("ouLGAA00001", "deSurvey001")] == "5"
    assert v[("ouLGAA00001", "dePoints001")] == "5" and v[("ouLGAA00001", "deCover0001")] == "100"
    assert int(v[("ouLGAA00001", "deFlagHi001")]) >= 5
    assert v[("ouLGAB00001", "deVisits001")] == "0"  # LGA B: nothing happened
    assert len(body["dataValues"]) == 2 * 5 * 2  # org units × indicators × days
    assert rep["imported"] == 20


def test_dhis2_config_problems_are_explained(
    placed: dict, db: Session, fake: Callable[..., FakeServer], monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: F811
    fake(dhis2_ok)
    enable(
        db,
        placed,
        "dhis2",
        url="https://dhis2.test",
        username="ragoh",
        org_units={"LGA-A": "ou1"},
        data_elements={"visits_completed": "de1"},
    )
    (rep,) = run_all(db, now=NOW)
    assert "RAGOH_DHIS2_PASSWORD" in rep["error"]
    monkeypatch.setattr(get_settings(), "dhis2_password", "x")
    enable(
        db,
        placed,
        "dhis2",
        url="https://dhis2.test",
        username="ragoh",
        org_units={"NOPE": "ou1"},
        data_elements={"visits_completed": "de1"},
    )
    (rep,) = run_all(db, now=NOW, force=True)
    assert "Unknown RAGOH area codes" in rep["error"]
    enable(
        db,
        placed,
        "dhis2",
        url="https://dhis2.test",
        username="ragoh",
        org_units={"LGA-A": "ou1"},
        data_elements={"made_up": "de1"},
    )
    (rep,) = run_all(db, now=NOW, force=True)
    assert "Unknown indicators" in rep["error"]


def test_dhis2_import_conflicts_fail_the_run(
    client: TestClient,
    placed: dict,
    db: Session,
    fake: Callable[..., FakeServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: F811
    monkeypatch.setattr(get_settings(), "dhis2_password", "x")
    enable(
        db,
        placed,
        "dhis2",
        url="https://dhis2.test",
        username="ragoh",
        org_units={"LGA-A": "ou1"},
        data_elements={"visits_completed": "de1"},
    )
    fake(
        lambda r: httpx.Response(
            200,
            json={
                "status": "ERROR",
                "importCount": {"imported": 0},
                "conflicts": [{"object": "ou1", "value": "Org unit not in data set"}],
            },
        )
    )
    (rep,) = run_all(db, now=NOW)
    assert rep["status"] == "error" and "Org unit not in data set" in rep["error"]


# --- DIGIT HCM ---------------------------------------------------------------------------
def digit_server(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/user/oauth/token":
        return httpx.Response(
            200, json={"access_token": "tok-123", "UserRequest": {"uuid": "u-1", "tenantId": "ng"}}
        )
    return httpx.Response(200, json={"ResponseInfo": {"status": "successful"}})


def test_digit_sends_events_with_request_info_once(
    client: TestClient,
    placed: dict,  # noqa: F811
    db: Session,
    fake: Callable[..., FakeServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "digit_password", "eGov@123")
    push_day(client, placed, "honest")
    enable(
        db,
        placed,
        "digit",
        url="https://hcm.test",
        endpoint="/ragoh-adapter/v1/events/_create",
        tenant_id="ng",
        username="RAGOH_SVC",
    )
    server = fake(digit_server)
    (rep,) = run_all(db, now=NOW)
    assert rep["status"] == "ok", rep
    token, post = server.requests
    assert (
        token.url.path == "/user/oauth/token"
        and b"tenantId=ng" in token.content
        and b"grant_type=password" in token.content
    )
    assert token.headers["authorization"] == "Basic ZWdvdi11c2VyLWNsaWVudDo="
    body = json.loads(post.content)
    assert post.url.path == "/ragoh-adapter/v1/events/_create"
    assert (
        body["RequestInfo"]["authToken"] == "tok-123"
        and body["RequestInfo"]["userInfo"]["uuid"] == "u-1"
    )
    visits = [e for e in body["events"] if e["type"] == "visit_outcome"]
    assert len(visits) == 5 and all(
        e["status"] == "completed" and e["point_external_id"].startswith("T") for e in visits
    )
    assert all(e["submission_id"] and e["collector_username"] == "col_a" for e in visits)

    server.requests.clear()
    (rep,) = run_all(db, now=NOW + timedelta(minutes=20))
    assert rep["events_sent"] == 0 and server.requests == []  # nothing resent, no sign-in needed


def test_digit_hmac_webhook_is_signed(
    client: TestClient,
    placed: dict,  # noqa: F811
    db: Session,
    fake: Callable[..., FakeServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "webhook_secret", "s3cret")
    push_day(client, placed, "honest")
    enable(db, placed, "digit", url="https://adapter.test", endpoint="/events", auth="hmac")
    server = fake(lambda r: httpx.Response(202))
    (rep,) = run_all(db, now=NOW)
    assert rep["status"] == "ok"
    (req,) = server.requests
    expected = hmac.new(b"s3cret", req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-ragoh-signature"] == f"sha256={expected}"
    assert "RequestInfo" not in json.loads(req.content)


def test_digit_failure_keeps_events_for_next_time(
    client: TestClient,
    placed: dict,
    db: Session,
    fake: Callable[..., FakeServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: F811
    monkeypatch.setattr(get_settings(), "webhook_secret", "s3cret")
    push_day(client, placed, "honest")
    enable(db, placed, "digit", url="https://adapter.test", endpoint="/events", auth="hmac")
    fake(lambda r: httpx.Response(503))
    assert run_all(db, now=NOW)[0]["status"] == "error"
    server = fake(lambda r: httpx.Response(200))
    (rep,) = run_all(db, now=NOW + timedelta(minutes=20))
    assert rep["status"] == "ok" and rep["events_sent"] >= 5 and len(server.requests) == 1


# --- Admin API ----------------------------------------------------------------------------
def test_integrations_admin_api(
    client: TestClient,
    placed: dict,  # noqa: F811
    db: Session,
    fake: Callable[..., FakeServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    push_day(client, placed, "honest")
    assert client.get(f"{API}/integrations", headers=auth(client, "manager_a")).status_code == 403
    rows = client.get(f"{API}/integrations", headers=auth(client, "admin")).json()
    assert {r["name"] for r in rows} == {"elasticsearch", "dhis2", "digit"} and not any(
        r["enabled"] for r in rows
    )

    cid = str(placed["campaign"].id)
    assert (
        client.post(
            f"{API}/integrations/dhis2/run",
            params={"campaign_id": cid},
            headers=auth(client, "admin"),
        ).status_code
        == 409
    )
    preview = client.get(
        f"{API}/integrations/dhis2/preview",
        params={"campaign_id": cid, "date": START.astimezone().date().isoformat()},
        headers=auth(client, "admin"),
    ).json()
    lga_a = next(p for p in preview if p["area_code"] == "LGA-A")
    assert lga_a["values"]["visits_completed"] == 5 and lga_a["org_unit"] is None

    monkeypatch.setattr(get_settings(), "dhis2_password", "x")
    enable(
        db,
        placed,
        "dhis2",
        url="https://dhis2.test",
        username="ragoh",
        org_units={"LGA-A": "ou1"},
        data_elements={"surveys": "de1"},
    )
    fake(dhis2_ok)
    r = client.post(
        f"{API}/integrations/dhis2/run", params={"campaign_id": cid}, headers=auth(client, "admin")
    )
    assert r.status_code == 200 and r.json()[0]["status"] == "ok"
    row = next(
        x
        for x in client.get(f"{API}/integrations", headers=auth(client, "admin")).json()
        if x["name"] == "dhis2"
    )
    assert (
        row["enabled"]
        and row["target"] == "dhis2.test"
        and row["secret_set"]
        and row["last_status"] == "ok"
    )
    assert row["last_stats"]["values_sent"] == 7  # 1 org unit × 1 indicator × 7 days
    _ = datetime.now(UTC)


def test_digit_keeps_delivered_batches_when_a_later_one_fails(
    client: TestClient,
    placed: dict,  # noqa: F811
    db: Session,
    fake: Callable[..., FakeServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.integrations import digit

    monkeypatch.setattr(get_settings(), "webhook_secret", "s3cret")
    monkeypatch.setattr(digit, "BATCH", 2)
    push_day(client, placed, "honest")  # 5 visit outcomes
    enable(db, placed, "digit", url="https://adapter.test", endpoint="/events", auth="hmac")
    calls = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200 if calls["n"] == 1 else 503)

    fake(flaky)
    (rep,) = run_all(db, now=NOW)
    assert rep["status"] == "error" and rep["events_sent"] == 2
    assert state(db, placed, "digit").cursor.get("visits")  # progress of batch 1 kept

    server = fake(lambda r: httpx.Response(200))
    (rep,) = run_all(db, now=NOW + timedelta(minutes=20))
    sent = [e["id"] for r in server.requests for e in json.loads(r.content)["events"]]
    assert rep["status"] == "ok" and len(sent) == 3  # only the three not yet delivered


def test_only_admins_see_or_change_where_data_is_sent(
    client: TestClient, placed: dict, db: Session
) -> None:  # noqa: F811
    cid = placed["campaign"].id
    enable(db, placed, "elasticsearch", url="https://es.internal")
    mgr = auth(client, "manager_a")
    cfg = client.get(f"{API}/campaigns/{cid}/config", headers=mgr).json()
    assert "integrations" not in cfg["overrides"] and "integrations" not in cfg["effective"]
    # A manager changing other settings keeps the integrations untouched...
    r = client.put(f"{API}/campaigns/{cid}/config", json={"geofence_m": 80}, headers=mgr)
    assert r.status_code == 200
    db.refresh(placed["campaign"])
    assert (
        placed["campaign"].config["integrations"]["elasticsearch"]["url"] == "https://es.internal"
    )
    # ...and cannot redirect them.
    evil = {"integrations": {"elasticsearch": {"enabled": True, "url": "https://attacker.example"}}}
    assert client.put(f"{API}/campaigns/{cid}/config", json=evil, headers=mgr).status_code == 403
    assert (
        client.put(
            f"{API}/campaigns/{cid}/config", json=evil, headers=auth(client, "admin")
        ).status_code
        == 200
    )
