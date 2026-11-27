"""Phase 6: live supervision — heartbeats, team view, reassignment, push notifications,
alerts and device-to-device relays."""

import base64
import json
import os
import uuid
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Assignment,
    AssignmentStatus,
    AuditLog,
    Device,
    Heartbeat,
    Message,
    Notification,
    Role,
    Shift,
    Submission,
    TeamMember,
    TeamRole,
    User,
)
from app.maintenance import watch_alerts
from app.services import push as push_service
from app.services.flags import evaluate_day
from tests.conftest import PASSWORD, auth
from tests.test_flags import push_day
from tests.test_tracking import DAY, placed  # noqa: F401 — fixture

API = "/api/v1"


@pytest.fixture
def expo() -> Iterator[list[dict]]:  # type: ignore[type-arg]
    """Fake Expo push service; collects every message sent."""
    sent: list[dict] = []  # type: ignore[type-arg]

    def handler(request: httpx.Request) -> httpx.Response:
        msgs = json.loads(request.content)
        sent.extend(msgs)
        data = [
            {"status": "error", "details": {"error": "DeviceNotRegistered"}}
            if m["to"].endswith("dead]")
            else {"status": "ok", "id": str(uuid.uuid4())}
            for m in msgs
        ]
        return httpx.Response(200, json={"data": data})

    push_service.transport = httpx.MockTransport(handler)
    yield sent
    push_service.transport = None


def register_token(client: TestClient, username: str, token: str) -> str:
    device = str(uuid.uuid4())
    r = client.post(
        f"{API}/devices/push-token",
        json={"device_id": device, "token": token},
        headers=auth(client, username),
    )
    assert r.status_code == 204, r.text
    return device


def beat(client: TestClient, username: str, **kw: object) -> None:
    body = {
        "device_id": str(uuid.uuid4()),
        "recorded_at": datetime.now(UTC).isoformat(),
        "lat": 8.30,
        "lon": 7.10,
        "accuracy_m": 8,
        "battery_pct": 64,
        "shift_active": True,
        "pending_count": 3,
        **kw,
    }
    r = client.post(f"{API}/heartbeat", json=body, headers=auth(client, username))
    assert r.status_code == 200, r.text
    assert r.json()["heartbeat_every_s"] == 180


def live(client: TestClient, username: str) -> dict:  # type: ignore[type-arg]
    r = client.get(f"{API}/team/live", headers=auth(client, username))
    assert r.status_code == 200, r.text
    return {c["username"]: c for c in r.json()["collectors"]}


# --- Heartbeat and team live -----------------------------------------------------
def test_team_live_shows_only_my_collectors_with_status(client: TestClient, world: dict) -> None:
    beat(client, "col_a")
    beat(client, "col_b")
    mine = live(client, "sup_a")
    assert set(mine) == {"col_a"}  # col_b is in sup_b's team
    c = mine["col_a"]
    assert c["status"] == "moving"
    assert c["on_shift"] is True
    assert (c["lat"], c["lon"], c["battery_pct"], c["pending_sync"]) == (8.30, 7.10, 64, 3)
    assert set(live(client, "sup_b")) == {"col_b"}
    assert set(live(client, "admin")) == {"col_a", "col_b"}


def test_collectors_cannot_see_team_live(client: TestClient, world: dict) -> None:
    r = client.get(f"{API}/team/live", headers=auth(client, "col_a"))
    assert r.status_code == 403


def test_stale_heartbeat_is_offline_and_old_beats_dont_overwrite(
    client: TestClient, world: dict, db: Session
) -> None:
    beat(client, "col_a", recorded_at=(datetime.now(UTC) - timedelta(minutes=25)).isoformat())
    assert live(client, "sup_a")["col_a"]["status"] == "offline"
    beat(client, "col_a", lat=8.31)
    beat(
        client, "col_a", lat=8.99, recorded_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat()
    )
    c = live(client, "sup_a")["col_a"]
    assert c["status"] == "moving" and c["lat"] == 8.31  # the delayed old beat lost
    # A phone clock in the future is clamped to now.
    beat(client, "col_a", recorded_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
    hb = db.get(Heartbeat, world["col_a"].id)
    assert hb is not None and hb.recorded_at <= datetime.now(UTC)


def test_never_seen_off_shift_and_at_point(client: TestClient, placed: dict) -> None:  # noqa: F811
    assert live(client, "sup_a")["col_a"]["status"] == "never_seen"
    beat(client, "col_a", shift_active=False)
    assert live(client, "sup_a")["col_a"]["status"] == "off_shift"
    house = placed["sim_points"][2]
    beat(client, "col_a", lat=house.lat, lon=house.lon + 0.0001)  # ~11 m away
    c = live(client, "sup_a")["col_a"]
    assert c["status"] == "at_point" and c["at_point_name"] == "House 2"
    assert c["assigned_today"] == 5 and c["done_today"] == 0


# --- Reassignment ----------------------------------------------------------------
@pytest.fixture
def col_a2(db: Session, world: dict) -> User:
    from app.core.security import hash_password

    u = User(
        organisation_id=world["org"].id, username="col_a2", full_name="Col A2",
        role=Role.collector, password_hash=hash_password(PASSWORD),
    )  # fmt: skip
    db.add(u)
    db.flush()
    db.add(TeamMember(team_id=world["team_a"].id, user_id=u.id, role_in_team=TeamRole.collector))
    db.flush()
    return u


def test_reassign_moves_only_open_points_within_the_team(
    client: TestClient,
    placed: dict,  # noqa: F811
    col_a2: User,
    db: Session,
) -> None:
    ids = [p.assignment_id for p in placed["sim_points"]]
    done = db.get(Assignment, uuid.UUID(ids[0]))
    assert done is not None
    done.status = AssignmentStatus.completed
    db.flush()
    r = client.get(
        f"{API}/team/assignments",
        params={"collector_id": str(placed["col_a"].id)},
        headers=auth(client, "sup_a"),
    )
    assert r.status_code == 200 and len(r.json()) == 4  # the completed one isn't offered

    r = client.post(
        f"{API}/assignments/reassign",
        json={"assignment_ids": ids[:3] + [str(uuid.uuid4())], "to_collector_id": str(col_a2.id),
              "reason": "col_a is sick"},
        headers=auth(client, "sup_a"),
    )  # fmt: skip
    assert r.status_code == 200, r.text
    out = r.json()
    assert sorted(out["moved"]) == sorted(ids[1:3])
    reasons = {s["reason"] for s in out["skipped"]}
    assert reasons == {"already completed", "not found"}
    for i in ids[1:3]:
        a = db.get(Assignment, uuid.UUID(i))
        assert a is not None and a.collector_id == col_a2.id
    assert db.scalar(select(AuditLog).where(AuditLog.action == "assignments_reassigned"))
    kinds = {(n.user_id, n.kind) for n in db.scalars(select(Notification))}
    assert (col_a2.id, "reassign") in kinds and (placed["col_a"].id, "reassign") in kinds

    # The new collector's phone gets them at next sync; the old one's no longer does.
    pulled = client.get(f"{API}/sync/pull", headers=auth(client, "col_a2")).json()
    assert {a["id"] for a in pulled["assignments"]} == set(ids[1:3])


def test_reassign_refuses_other_teams(client: TestClient, placed: dict) -> None:  # noqa: F811
    ids = [p.assignment_id for p in placed["sim_points"]]
    r = client.post(
        f"{API}/assignments/reassign",
        json={"assignment_ids": ids[:1], "to_collector_id": str(placed["col_b"].id)},
        headers=auth(client, "sup_a"),
    )
    assert r.status_code == 404
    r = client.post(
        f"{API}/assignments/reassign",
        json={"assignment_ids": ids[:1], "to_collector_id": str(placed["col_b"].id)},
        headers=auth(client, "sup_b"),
    )
    assert r.status_code == 200 and r.json()["moved"] == []  # not sup_b's point


# --- Push notifications ----------------------------------------------------------
def test_high_flag_pushes_to_the_supervisor(
    client: TestClient,
    placed: dict,  # noqa: F811
    db: Session,
    expo: list[dict],  # type: ignore[type-arg]
) -> None:
    """Phase 6 'done when': the supervisor's phone is told about a high-severity flag."""
    register_token(client, "sup_a", "ExponentPushToken[sup-a]")
    push_day(client, placed, "cheater")
    to_sup = [m for m in expo if m["to"] == "ExponentPushToken[sup-a]"]
    assert to_sup, "no push sent"
    assert all(m["data"]["kind"] == "flag" for m in to_sup)
    assert any("Col_A" in m["title"] for m in to_sup)
    assert all(m["data"]["flag_id"] for m in to_sup)
    n_before = len(expo)
    evaluate_day(db, placed["col_a"].id, DAY)  # same day re-processed: no repeat alerts
    push_service.deliver(db)
    assert len(expo) == n_before
    sent = db.scalars(select(Notification).where(Notification.user_id == placed["sup_a"].id))
    assert all(n.status == "sent" for n in sent)


def test_messages_push_to_recipients_and_dead_tokens_are_dropped(
    client: TestClient,
    world: dict,
    db: Session,
    expo: list[dict],  # type: ignore[type-arg]
) -> None:
    dev = register_token(client, "col_a", "ExponentPushToken[dead]")
    r = client.post(
        f"{API}/messages",
        json={"recipient_id": str(world["col_a"].id), "body": "Call me please"},
        headers=auth(client, "sup_a"),
    )
    assert r.status_code == 201
    assert expo[-1]["body"] == "Call me please" and expo[-1]["title"] == "Message from Sup_A"
    device = db.get(Device, uuid.UUID(dev))
    assert device is not None and device.push_token is None  # uninstalled app forgotten

    register_token(client, "col_a", "ExponentPushToken[col-a]")
    client.post(
        f"{API}/messages",
        json={"team_id": str(world["team_a"].id), "body": "Meet at the clinic at 2"},
        headers=auth(client, "sup_a"),
    )
    assert expo[-1]["to"] == "ExponentPushToken[col-a]"  # broadcast reaches the team


def test_push_token_must_be_an_expo_token(client: TestClient, world: dict) -> None:
    r = client.post(
        f"{API}/devices/push-token",
        json={"device_id": str(uuid.uuid4()), "token": "fcm:abc"},
        headers=auth(client, "col_a"),
    )
    assert r.status_code == 422


def test_watch_alerts_offline_and_not_started(
    client: TestClient,
    placed: dict,  # noqa: F811
    db: Session,
    expo: list[dict],  # type: ignore[type-arg]
) -> None:
    register_token(client, "sup_a", "ExponentPushToken[sup-a]")
    now = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)  # 11:00 in Lagos
    # Not started: past 09:00, five open points, no shift.
    counts = watch_alerts(db, now)
    assert counts["not_started"] == 1 and counts["offline"] == 0
    # Started, then silent for 45 minutes.
    db.add(Shift(id=uuid.uuid4(), user_id=placed["col_a"].id, started_at=now - timedelta(hours=2)))
    db.add(
        Heartbeat(
            user_id=placed["col_a"].id, recorded_at=now - timedelta(minutes=45),
            shift_active=True, pending_count=0,
        )
    )  # fmt: skip
    db.flush()
    counts = watch_alerts(db, now)
    assert counts["offline"] == 1 and counts["not_started"] == 0
    assert watch_alerts(db, now + timedelta(minutes=10))["offline"] == 0  # same silence
    kinds = [m["data"]["kind"] for m in expo]
    assert kinds.count("alert_not_started") == 1 and kinds.count("alert_offline") == 1
    # Quiet hours: nothing after 20:00 local.
    db.get(Heartbeat, placed["col_a"].id).recorded_at = now  # type: ignore[union-attr]
    assert watch_alerts(db, now + timedelta(hours=10))["offline"] == 0


# --- Device-to-device relay ------------------------------------------------------
def seal(key: bytes, collector_id: uuid.UUID, payload: dict) -> dict:  # type: ignore[type-arg]
    nonce = os.urandom(12)
    ct = ChaCha20Poly1305(key).encrypt(
        nonce, zlib.compress(json.dumps(payload).encode()), collector_id.bytes
    )
    return {
        "id": str(uuid.uuid4()),
        "collector_id": str(collector_id),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ct).decode(),
        "scanned_at": datetime.now(UTC).isoformat(),
    }


def relay_push(client: TestClient, username: str, relays: list[dict]) -> dict:  # type: ignore[type-arg]
    r = client.post(
        f"{API}/sync/push",
        json={"device": {"id": str(uuid.uuid4())}, "device_time": datetime.now(UTC).isoformat(),
              "relays": relays},
        headers=auth(client, username),
    )  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def collector_bundle(placed: dict, made_at: datetime) -> tuple[dict, str]:  # type: ignore[type-arg]
    """What the collector's phone seals: a finished visit and a note to the supervisor."""
    sub_id = str(uuid.uuid4())
    a = placed["sim_points"][0]
    loc = {"lat": a.lat, "lon": a.lon, "accuracy_m": 5}
    return {
        "device": {"id": str(uuid.uuid4()), "app_version": "0.6.0"},
        "device_time": made_at.isoformat(),
        "submissions": [{
            "id": sub_id, "form_version_id": str(placed["fv"].id), "assignment_id": a.assignment_id,
            "started_at": (made_at - timedelta(minutes=20)).isoformat(),
            "ended_at": (made_at - timedelta(minutes=5)).isoformat(),
            "start_location": loc, "end_location": loc,
            "data": {"consent": "yes", "hh_size": 4, "under5": 1, "has_net": "yes"},
        }],
        "assignment_updates": [{"id": str(uuid.uuid4()), "assignment_id": a.assignment_id,
                                "status": "completed", "changed_at": made_at.isoformat()}],
        "messages": [{"id": str(uuid.uuid4()), "recipient_id": str(placed["sup_a"].id),
                      "body": "No network here, sending via you", "sent_at": made_at.isoformat()}],
    }, sub_id  # fmt: skip


def test_relay_carries_collector_data_through_the_supervisor(
    client: TestClient,
    placed: dict,  # noqa: F811
    db: Session,
) -> None:
    """Phase 6 'done when': a collector's data reaches the server via the supervisor's phone,
    sealed so the supervisor's phone can neither read nor alter it."""
    r = client.get(f"{API}/me/relay-key", headers=auth(client, "col_a"))
    key = base64.b64decode(r.json()["key"])
    assert len(key) == 32
    again = client.get(f"{API}/me/relay-key", headers=auth(client, "col_a")).json()["key"]
    assert base64.b64decode(again) == key  # stable

    made_at = datetime.now(UTC) - timedelta(hours=3)  # sealed in the morning, offline
    payload, sub_id = collector_bundle(placed, made_at)
    relay = seal(key, placed["col_a"].id, payload)
    relay["scanned_at"] = (made_at + timedelta(minutes=1)).isoformat()
    out = relay_push(client, "sup_a", [relay])
    assert out["accepted"]["relays"] == [relay["id"]] and out["rejected"] == []

    sub = db.get(Submission, uuid.UUID(sub_id))
    assert sub is not None and sub.user_id == placed["col_a"].id  # stored as the collector's
    assert abs(sub.clock_offset_s or 0) < 120  # an old bundle is not a wrong clock
    a = db.get(Assignment, uuid.UUID(placed["sim_points"][0].assignment_id))
    assert a is not None and a.status == AssignmentStatus.completed
    msg = db.scalar(select(Message).where(Message.sender_id == placed["col_a"].id))
    assert msg is not None and msg.recipient_id == placed["sup_a"].id
    assert db.scalar(select(AuditLog).where(AuditLog.action == "relay_received"))

    # Replaying the same bundle (or the collector later syncing the same records) is harmless.
    assert relay_push(client, "sup_a", [relay])["accepted"]["relays"] == [relay["id"]]
    assert len(list(db.scalars(select(Submission).where(Submission.id == uuid.UUID(sub_id))))) == 1


def test_relay_rejects_tampering_and_strangers(client: TestClient, placed: dict) -> None:  # noqa: F811
    key = base64.b64decode(
        client.get(f"{API}/me/relay-key", headers=auth(client, "col_a")).json()["key"]
    )
    payload, _ = collector_bundle(placed, datetime.now(UTC))
    good = seal(key, placed["col_a"].id, payload)

    tampered = {**good, "id": str(uuid.uuid4())}
    ct = bytearray(base64.b64decode(good["ciphertext"]))
    ct[5] ^= 1
    tampered["ciphertext"] = base64.b64encode(bytes(ct)).decode()
    wrong_key = seal(os.urandom(32), placed["col_a"].id, payload)
    swapped = {**good, "id": str(uuid.uuid4()), "collector_id": str(placed["col_b"].id)}

    out = relay_push(client, "sup_a", [tampered, wrong_key, swapped])
    assert out["accepted"]["relays"] == []
    errs = {x["id"]: x["error"] for x in out["rejected"]}
    assert "could not be opened" in errs[tampered["id"]]
    assert "could not be opened" in errs[wrong_key["id"]]
    assert errs[swapped["id"]] == "not your collector"  # col_b isn't sup_a's

    # Another team's supervisor can't relay for col_a; nor can a collector.
    assert relay_push(client, "sup_b", [good])["rejected"][0]["error"] == "not your collector"
    assert relay_push(client, "col_b", [good])["rejected"][0]["error"] == "not your collector"


def test_pull_gives_supervisors_their_roster(client: TestClient, world: dict) -> None:
    sup = client.get(f"{API}/sync/pull", headers=auth(client, "sup_a")).json()
    assert [m["username"] for m in sup["team_members"]] == ["col_a"]
    col = client.get(f"{API}/sync/pull", headers=auth(client, "col_a")).json()
    assert col["team_members"] == []


def test_route_between_two_positions(client: TestClient, world: dict) -> None:
    r = client.get(
        f"{API}/routes/between",
        params={"lat": 8.30, "lon": 7.10, "to_lat": 8.31, "to_lon": 7.11},
        headers=auth(client, "sup_a"),
    )
    assert r.status_code == 200
    assert r.json()["distance_m"] > 1000


# Produced by packages/sync-core (noble chacha20poly1305 + fflate zlib) with key = bytes 0..31,
# nonce = bytes 100..111. Proves the phone's sealing and the server's opening agree.
TS_NONCE = "ZGVmZ2hpamtsbW5v"
TS_CIPHERTEXT = (
    "TMvmRUPPtWL3WEoaCjPCHQMI+ZjIS8hV5lhwiMuDl+vvVoxF2m1ii115P6QX1I3O65WsBrGnL99uEZG7aDsB6TPt"
    "9zbeEktr3nDaLskiYQzXUV7TQ0GEYNFaPbvtkYCro3C5XDZIm3g4k1j11hCScP58nBL0izfQU1uu+EJLFuxX4r63"
    "/WNek1fZUA4inKhOXP1j9B5Qem0Fe5W51EX1QzDUvwv+/v9wEoG26SjpxsrRu/vaNLJnKzc9RiwcM0/j7Fi6tshT"
    "fbvqI34UbGsCGXb6UJgBWB4ywWcdTbHyslKcxtSKOQLtwsTc/+tSVBAKsQqSKdTDyb66fqlj9N8="
)


def test_server_opens_a_bundle_sealed_by_the_phone_library() -> None:
    from app.api.v1.sync import open_relay
    from app.schemas import RelayIn

    relay = RelayIn(
        id=uuid.uuid4(),
        collector_id=uuid.UUID("6f1c2a9e-4b7d-4c1e-9a3f-2d8e5b6a7c90"),
        nonce=TS_NONCE,
        ciphertext=TS_CIPHERTEXT,
        scanned_at=datetime.now(UTC),
    )
    body = open_relay(bytes(range(32)), relay)
    assert body.messages[0].body == "from TS"
    assert str(body.device.id) == "0b9c2f5e-1d2a-4e3b-8c4d-5e6f7a8b9c0d"


def test_supervisor_phone_messages_only_reach_visible_users(
    client: TestClient, world: dict
) -> None:
    now = datetime.now(UTC).isoformat()
    ok, bad = str(uuid.uuid4()), str(uuid.uuid4())
    r = client.post(
        f"{API}/sync/push",
        json={"device": {"id": str(uuid.uuid4())}, "device_time": now, "messages": [
            {"id": ok, "recipient_id": str(world["col_a"].id), "body": "hi", "sent_at": now},
            {"id": bad, "recipient_id": str(world["col_b"].id), "body": "hi", "sent_at": now},
        ]},
        headers=auth(client, "sup_a"),
    )  # fmt: skip
    assert r.json()["accepted"]["messages"] == [ok]
    assert r.json()["rejected"][0]["id"] == bad
