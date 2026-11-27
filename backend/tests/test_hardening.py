"""Phase 8: privacy notice, lost-phone block/wipe, audit viewer, retention, data savers,
battery/data report, logging."""

import gzip
import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import JsonFormatter, scrub_event
from app.db.models import AuditLog, Device, DeviceEvent, Notification, RefreshToken
from app.maintenance import apply_retention
from tests.conftest import PASSWORD, auth

API = "/api/v1"


def push(client: TestClient, user: str, device: str, **records: object) -> object:
    return client.post(
        f"{API}/sync/push",
        json={
            "device": {"id": device, "model": "Tecno Spark 10"},
            "device_time": datetime.now(UTC).isoformat(),
            **records,
        },
        headers=auth(client, user),
    )


# --- Privacy notice ---------------------------------------------------------------
def test_privacy_notice_must_be_accepted_and_follows_config(
    client: TestClient, world: dict, db: Session
) -> None:
    r = client.get(f"{API}/me/privacy", headers=auth(client, "col_a")).json()
    assert r["needs_acceptance"] is True and r["accepted_version"] is None
    text = " ".join(s["body"] for s in r["notice"]["sections"])
    assert "90 days" in text and "Only during a shift" in text
    v = r["notice"]["version"]

    client.post(f"{API}/me/consent", json={"notice_version": v}, headers=auth(client, "col_a"))
    r = client.get(f"{API}/me/privacy", headers=auth(client, "col_a")).json()
    assert r["needs_acceptance"] is False and r["accepted_version"] == v

    # A new wording (new version) or retention period must be shown again.
    world["campaign"].config = {
        "retention": {"raw_gps_days": 60},
        "privacy": {"notice_version": "3"},
    }
    db.flush()
    r = client.get(f"{API}/me/privacy", headers=auth(client, "col_a")).json()
    assert r["needs_acceptance"] is True
    assert "deleted after 60 days" in " ".join(s["body"] for s in r["notice"]["sections"])


# --- Lost or stolen phone -----------------------------------------------------------
def test_block_and_wipe_a_lost_phone(client: TestClient, world: dict, db: Session) -> None:
    device = str(uuid.uuid4())
    login = client.post(
        f"{API}/auth/login", json={"username": "col_a", "password": PASSWORD, "device_id": device}
    ).json()
    assert push(client, "col_a", device).status_code == 200  # type: ignore[attr-defined]

    # Another team's supervisor can't touch it; collectors can't at all.
    body = {"reason": "Phone stolen at the market", "wipe": True}
    assert (
        client.post(
            f"{API}/devices/{device}/block", json=body, headers=auth(client, "sup_b")
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{API}/devices/{device}/block", json=body, headers=auth(client, "col_b")
        ).status_code
        == 403
    )

    r = client.post(f"{API}/devices/{device}/block", json=body, headers=auth(client, "sup_a"))
    assert r.status_code == 200 and r.json()["is_blocked"] and r.json()["wipe_requested"]
    # Signed out: its refresh token is revoked; syncing and signing in again are refused.
    assert (
        client.post(
            f"{API}/auth/refresh", json={"refresh_token": login["refresh_token"]}
        ).status_code
        == 401
    )
    assert push(client, "col_a", device).status_code == 423  # type: ignore[attr-defined]
    again = client.post(
        f"{API}/auth/login", json={"username": "col_a", "password": PASSWORD, "device_id": device}
    )
    assert again.status_code == 423
    # Without any sign-in, the phone learns it must erase itself, then confirms.
    assert client.post(f"{API}/devices/status", json={"device_id": device}).json() == {
        "blocked": True,
        "wipe": True,
    }
    assert (
        client.post(f"{API}/devices/status", json={"device_id": device, "wiped": True}).json()[
            "wipe"
        ]
        is False
    )
    d = db.get(Device, uuid.UUID(device))
    assert d is not None and d.wiped_at is not None
    actions = {a.action for a in db.scalars(select(AuditLog))}
    assert {"device_block", "device_wiped", "login_blocked_device"} <= actions
    # An unknown id reveals nothing.
    assert client.post(f"{API}/devices/status", json={"device_id": str(uuid.uuid4())}).json() == {
        "blocked": False,
        "wipe": False,
    }

    listed = client.get(f"{API}/devices", headers=auth(client, "sup_a")).json()
    assert [x["id"] for x in listed] == [device] and listed[0]["username"] == "col_a"
    assert client.get(f"{API}/devices", headers=auth(client, "sup_b")).json() == []
    r = client.post(f"{API}/devices/{device}/unblock", headers=auth(client, "sup_a"))
    assert r.json()["is_blocked"] is False
    assert db.scalar(
        select(RefreshToken).where(RefreshToken.device_id == uuid.UUID(device))
    ).revoked_at  # type: ignore[union-attr]


def test_device_list_counts_app_crashes(client: TestClient, world: dict) -> None:
    device = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    push(client, "col_a", device, device_events=[
        {"id": str(uuid.uuid4()), "type": "app_error", "occurred_at": now, "details": {"message": "TypeError: x"}},
        {"id": str(uuid.uuid4()), "type": "app_error", "occurred_at": now, "details": {"message": "TypeError: y"}},
    ])  # fmt: skip
    (d,) = client.get(f"{API}/devices", headers=auth(client, "sup_a")).json()
    assert d["app_errors_7d"] == 2 and d["model"] == "Tecno Spark 10"


# --- Audit log viewer ----------------------------------------------------------------
def test_audit_log_viewer_is_scoped(client: TestClient, world: dict) -> None:
    today = date.today().isoformat()
    for who in ("col_a", "col_b"):
        uid = world[who].id
        client.get(
            f"{API}/collectors/{uid}/day", params={"date": today}, headers=auth(client, "admin")
        )

    r = client.get(
        f"{API}/audit-logs", params={"action": "track_view"}, headers=auth(client, "admin")
    ).json()
    assert r["total"] == 2 and {i["entity_name"] for i in r["items"]} == {"Col_A", "Col_B"}
    assert all(i["actor_name"] == "Admin" for i in r["items"])
    m = client.get(
        f"{API}/audit-logs", params={"action": "track_view"}, headers=auth(client, "manager_a")
    ).json()
    assert [i["entity_name"] for i in m["items"]] == ["Col_A"]
    p = client.get(
        f"{API}/audit-logs", params={"privacy_only": True}, headers=auth(client, "admin")
    ).json()
    assert {i["action"] for i in p["items"]} == {"track_view"}
    assert client.get(f"{API}/audit-logs", headers=auth(client, "sup_a")).status_code == 403


# --- Retention -------------------------------------------------------------------------
def test_retention_covers_events_notifications_and_audit(db: Session, world: dict) -> None:
    old, recent = datetime.now(UTC) - timedelta(days=800), datetime.now(UTC) - timedelta(days=1)
    uid = world["col_a"].id
    for t in (old, recent):
        db.add(DeviceEvent(id=uuid.uuid4(), user_id=uid, type="gps_off", occurred_at=t, details={}))
        db.add(
            Notification(
                user_id=uid, kind="message", title="t", body="b", status="sent", created_at=t
            )
        )
        db.add(AuditLog(action="login", actor_id=uid, created_at=t, details={}))
    db.flush()
    out = apply_retention(db)
    assert (
        out["deleted_device_events"] == 1
        and out["deleted_notifications"] == 1
        and out["deleted_audit_logs"] == 1
    )
    assert len(db.scalars(select(DeviceEvent)).all()) == 1


# --- Mobile data savers ------------------------------------------------------------------
def test_gzip_compressed_uploads_are_accepted(client: TestClient, world: dict) -> None:
    pid = str(uuid.uuid4())
    body = {
        "device": {"id": str(uuid.uuid4())}, "device_time": datetime.now(UTC).isoformat(),
        "gps_points": [{"id": pid, "recorded_at": datetime.now(UTC).isoformat(), "lat": 8.3, "lon": 7.1}],
    }  # fmt: skip
    headers = {
        **auth(client, "col_a"),
        "Content-Type": "application/json",
        "Content-Encoding": "gzip",
    }
    r = client.post(
        f"{API}/sync/push", content=gzip.compress(json.dumps(body).encode()), headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"]["gps_points"] == [pid]
    bad = client.post(f"{API}/sync/push", content=b"not gzip", headers=headers)
    assert bad.status_code == 400


def test_unchanged_snapshot_is_not_downloaded_again(client: TestClient, world: dict) -> None:
    h = auth(client, "col_a")
    first = client.get(f"{API}/sync/pull", headers=h)
    tag = first.headers["etag"]
    same = client.get(f"{API}/sync/pull", headers={**h, "If-None-Match": tag})
    assert same.status_code == 304 and same.content == b""
    client.post(
        f"{API}/messages",
        json={"recipient_id": str(world["col_a"].id), "body": "New task"},
        headers=auth(client, "sup_a"),
    )
    changed = client.get(f"{API}/sync/pull", headers={**h, "If-None-Match": tag})
    assert changed.status_code == 200 and changed.headers["etag"] != tag
    assert changed.json()["messages"][0]["body"] == "New task"


# --- Battery and data report ------------------------------------------------------------
def test_device_usage_report(client: TestClient, world: dict) -> None:
    now = datetime.now(UTC)

    def summary(start: int, end: int, minutes: int, mb: float, charged: bool = False) -> dict:  # type: ignore[type-arg]
        return {"id": str(uuid.uuid4()), "type": "shift_summary", "occurred_at": now.isoformat(),
                "details": {"battery_start": start, "battery_end": end, "minutes": minutes, "charged": charged,
                            "gps_points": minutes * 4, "bytes_up": mb * 0.8e6, "bytes_down": mb * 0.2e6}}  # fmt: skip

    push(client, "col_a", str(uuid.uuid4()), device_events=[
        summary(95, 75, 480, 0.6),  # 20% over 8 h
        summary(50, 80, 240, 0.3, charged=True),  # charging: ignored for battery
    ])  # fmt: skip
    today = now.date().isoformat()
    r = client.get(
        f"{API}/analytics/device-usage",
        params={"date_from": today, "date_to": today},
        headers=auth(client, "sup_a"),
    )
    assert r.status_code == 200, r.text
    (row,) = r.json()["collectors"]
    assert row["shifts"] == 2 and row["hours"] == 12.0
    assert row["battery_per_hour"] == 2.5 and row["battery_8h"] == 20
    assert row["mb_per_day"] == 0.9 and row["phone_models"] == ["Tecno Spark 10"]
    assert r.json()["overall_battery_8h"] == 20 and r.json()["target_battery_8h"] == 25


# --- Observability ------------------------------------------------------------------------
def test_sentry_events_are_scrubbed_of_personal_data() -> None:
    event = {
        "request": {"method": "POST", "url": "https://x/api/v1/sync/push?token=abc", "data": {"lat": 9.1}, "headers": {"Authorization": "Bearer x"}},
        "user": {"id": "u1", "ip_address": "1.2.3.4"},
        "exception": {"values": [{"stacktrace": {"frames": [{"function": "push", "vars": {"body": "secret"}}]}}]},
    }  # fmt: skip
    out = scrub_event(event, None)
    assert out["request"] == {"method": "POST", "url": "https://x/api/v1/sync/push"}
    assert "user" not in out
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]


def test_json_log_lines() -> None:
    rec = logging.LogRecord(
        "ragoh", logging.WARNING, __file__, 1, "Push failed: %s", ("timeout",), None
    )
    line = json.loads(JsonFormatter().format(rec))
    assert (
        line["level"] == "WARNING"
        and line["msg"] == "Push failed: timeout"
        and line["logger"] == "ragoh"
    )
