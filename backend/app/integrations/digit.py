"""DIGIT HCM: visit outcomes, supervision results and closed flags, sent as event batches.

Two modes:
- `auth: digit` — signs in like any DIGIT client (egov-user `/user/oauth/token`, password
  grant, tenant, user type) and wraps each batch in a DIGIT `RequestInfo` envelope with the
  token and user info, POSTed to `url + endpoint`.
- `auth: hmac` — a plain JSON webhook signed with `X-RAGOH-Signature: sha256=<hmac>` using
  RAGOH_WEBHOOK_SECRET, for a small adapter service on the HCM side.

The receiving HCM endpoint depends on the instance (and on what the HCM team wants to
store), so it is configuration, not code. Events carry each visit point's `external_id`:
import HCM households/boundaries into RAGOH with their HCM ids as `external_id` and the
events link straight back to HCM records.

Batch body (inside RequestInfo in digit mode):
    {"source": "ragoh", "campaign": "...", "tenantId": "...", "events": [
      {"type": "visit_outcome", "id": ..., "point_external_id": ..., "status": "completed", ...},
      {"type": "supervision", ...}, {"type": "flag_closed", ...}]}
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime
from typing import Any

from geoalchemy2.shape import to_shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Assignment,
    AssignmentStatus,
    Campaign,
    Flag,
    FlagStatus,
    Form,
    FormKind,
    FormVersion,
    Submission,
    Team,
    User,
    VisitPoint,
)
from app.integrations.base import LOOKBACK, ConfigError, PartialFailure, RunResult, client

BATCH = 200
EPOCH = "1970-01-02T00:00:00+00:00"
DONE = (AssignmentStatus.completed, AssignmentStatus.not_found, AssignmentStatus.refused)


def _token(h: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    s = get_settings()
    if not s.digit_password or not cfg.get("username") or not cfg.get("tenant_id"):
        raise ConfigError(
            "digit auth needs integrations.digit.username and tenant_id, and RAGOH_DIGIT_PASSWORD on the server"
        )
    r = h.post(
        "/user/oauth/token",
        data={
            "username": cfg["username"],
            "password": s.digit_password,
            "grant_type": "password",
            "scope": "read",
            "tenantId": cfg["tenant_id"],
            "userType": cfg.get("user_type") or "EMPLOYEE",
        },  # fmt: skip
        headers={"Authorization": f"Basic {s.digit_client_auth}"},
    )
    if r.status_code in (400, 401, 403):
        raise ConfigError(
            f"DIGIT sign-in refused ({r.status_code}): check username, tenant and password"
        )
    r.raise_for_status()
    body = r.json()
    if not body.get("access_token"):
        raise ConfigError("DIGIT sign-in returned no access_token")
    return body


def _events(
    db: Session, campaign: Campaign, cursor: dict[str, Any]
) -> tuple[list[tuple[str, datetime, dict[str, Any]]], dict[str, str]]:
    """(stream, timestamp, event) for everything new since the cursor, oldest first."""
    users = {
        u.id: u.username
        for u in db.scalars(select(User).where(User.organisation_id == campaign.organisation_id))
    }
    out: list[tuple[str, datetime, dict[str, Any]]] = []

    since = datetime.fromisoformat(cursor.get("visits", EPOCH)) - LOOKBACK
    for a, vp in db.execute(
        select(Assignment, VisitPoint)
        .join(VisitPoint, VisitPoint.id == Assignment.visit_point_id)
        .where(
            VisitPoint.campaign_id == campaign.id,
            Assignment.status.in_(DONE),
            Assignment.status_changed_at.is_not(None),
            Assignment.status_changed_at > since,
        )
    ).tuples():
        g = to_shape(vp.geom)
        sub = db.scalar(
            select(Submission.id)
            .where(Submission.assignment_id == a.id)
            .order_by(Submission.ended_at.desc())
            .limit(1)
        )
        assert a.status_changed_at is not None
        out.append(("visits", a.status_changed_at, {
            "type": "visit_outcome", "id": f"{a.id}:{a.status.value}", "assignment_id": str(a.id),
            "kind": a.kind.value, "point_external_id": vp.external_id, "point_name": vp.name,
            "point_location": {"lat": round(g.y, 6), "lon": round(g.x, 6)}, "status": a.status.value,
            "reason": a.status_reason, "collector_username": users.get(a.collector_id),
            "at": a.status_changed_at.isoformat(), "submission_id": str(sub) if sub else None,
        }))  # fmt: skip

    since = datetime.fromisoformat(cursor.get("supervision", EPOCH)) - LOOKBACK
    for s in db.scalars(
        select(Submission)
        .join(FormVersion, FormVersion.id == Submission.form_version_id)
        .join(Form, Form.id == FormVersion.form_id)
        .where(
            Form.campaign_id == campaign.id,
            Form.kind.in_([FormKind.supervision, FormKind.backcheck]),
            Submission.received_at > since,
        )
    ):
        g = to_shape(s.end_geom) if s.end_geom is not None else None
        out.append(("supervision", s.received_at, {
            "type": "supervision", "id": str(s.id), "supervisor_username": users.get(s.user_id),
            "collector_username": s.data.get("collector_username"), "at": s.ended_at.isoformat(),
            "location": {"lat": round(g.y, 6), "lon": round(g.x, 6)} if g else None, "answers": s.data,
        }))  # fmt: skip

    since = datetime.fromisoformat(cursor.get("flags", EPOCH)) - LOOKBACK
    for f in db.scalars(
        select(Flag).where(
            Flag.campaign_id == campaign.id,
            Flag.status.in_([FlagStatus.resolved, FlagStatus.dismissed]),
            Flag.resolved_at.is_not(None),
            Flag.resolved_at > since,
        )
    ):
        assert f.resolved_at is not None
        out.append(("flags", f.resolved_at, {
            "type": "flag_closed", "id": str(f.id), "rule": f.rule_id, "title": f.title,
            "severity": f.severity.value, "status": f.status.value, "collector_username": users.get(f.collector_id),
            "date": f.local_date.isoformat(), "comment": f.resolution_comment, "at": f.resolved_at.isoformat(),
        }))  # fmt: skip
    sent_recently = set(cursor.get("recent_ids", []))
    out = [e for e in out if e[2]["id"] not in sent_recently]
    out.sort(key=lambda e: e[1])
    teams = {t.id: t.name for t in db.scalars(select(Team).where(Team.campaign_id == campaign.id))}
    return out, {str(k): v for k, v in teams.items()}


def run(
    db: Session, campaign: Campaign, cfg: dict[str, Any], cursor: dict[str, Any], now: datetime
) -> RunResult:
    url, endpoint = cfg.get("url"), cfg.get("endpoint")
    mode = cfg.get("auth") or "digit"
    if not url or not endpoint:
        raise ConfigError(
            "Set integrations.digit.url and endpoint (the HCM path that receives RAGOH events)"
        )
    if mode == "hmac" and not get_settings().webhook_secret:
        raise ConfigError("hmac mode needs RAGOH_WEBHOOK_SECRET on the server")
    events, _ = _events(db, campaign, cursor)
    new_cursor = dict(cursor)
    recent: list[tuple[str, str, str]] = [
        (x[0], x[1], x[2]) for x in cursor.get("recent", []) if len(x) == 3
    ]
    sent = 0
    with client(base_url=str(url).rstrip("/")) as h:
        session = _token(h, cfg) if mode == "digit" and events else None
        for i in range(0, len(events), BATCH):
            chunk = events[i : i + BATCH]
            body: dict[str, Any] = {
                "source": "ragoh", "campaign": campaign.name, "tenantId": cfg.get("tenant_id"),
                "events": [e for _, _, e in chunk],
            }  # fmt: skip
            headers = {"Content-Type": "application/json"}
            if session is not None:
                body = {
                    "RequestInfo": {
                        "apiId": "ragoh", "ver": "1.0", "ts": int(time.time() * 1000), "action": "create",
                        "msgId": str(uuid.uuid4()), "authToken": session["access_token"],
                        "userInfo": session.get("UserRequest") or {},
                    },
                    **body,
                }  # fmt: skip
            raw = json.dumps(body, default=str).encode()
            if mode == "hmac":
                sig = hmac.new(
                    str(get_settings().webhook_secret).encode(), raw, hashlib.sha256
                ).hexdigest()
                headers["X-RAGOH-Signature"] = f"sha256={sig}"
            try:
                r = h.post(str(endpoint), content=raw, headers=headers)
                if r.status_code in (401, 403):
                    raise ConfigError(f"DIGIT refused the request ({r.status_code})")
                r.raise_for_status()
            except Exception as err:
                if sent:  # earlier batches arrived: keep that progress
                    raise PartialFailure(err, new_cursor, {"events_sent": sent}) from err
                raise
            # Advance each stream only past what was delivered, and remember recent ids so
            # the look-back window never sends an event twice.
            for stream, ts, e in chunk:
                if ts.isoformat() > new_cursor.get(stream, EPOCH):
                    new_cursor[stream] = ts.isoformat()
                recent.append((stream, ts.isoformat(), e["id"]))
            # An id can only come back while it is inside its stream's look-back window.
            recent = [
                (st_, t, i)
                for st_, t, i in recent
                if datetime.fromisoformat(t)
                >= datetime.fromisoformat(new_cursor.get(st_, EPOCH)) - LOOKBACK
            ]
            new_cursor["recent"] = recent
            new_cursor["recent_ids"] = [i for _, _, i in recent]
            sent += len(chunk)
    kinds: dict[str, int] = {}
    for _, _, e in events:
        kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    return RunResult(stats={"events_sent": sent, **kinds}, cursor=new_cursor)
