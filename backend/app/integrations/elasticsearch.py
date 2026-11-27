"""Elasticsearch / Kibana: submissions, stops, daily summaries and flags as documents.

Four indices (`<prefix>-submissions`, `-stops`, `-day-summaries`, `-flags`) with index
templates that map locations as `geo_point` (Kibana maps) and times as dates. Documents
use RAGOH ids, so re-sending is an update, never a duplicate. Each run sends what changed
since the last one; stops of a recomputed day are replaced as a whole (their ids change
when a day is recomputed).

Answers are stored under `answers` as a `flattened` field: every form can be searched and
filtered without one form's field types clashing with another's.
"""

import json
import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2.shape import to_shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    AdminArea,
    Campaign,
    CollectorDaySummary,
    Flag,
    Form,
    FormVersion,
    Stop,
    Submission,
    Team,
    TeamMember,
    User,
    VisitPoint,
)
from app.integrations.base import LOOKBACK, ConfigError, RunResult, client

TEMPLATE_VERSION = 1
BATCH = 500
EPOCH = "1970-01-02T00:00:00+00:00"

_common = {
    "campaign": {"type": "keyword"},
    "collector": {"type": "keyword"},
    "collector_id": {"type": "keyword"},
    "team": {"type": "keyword"},
}
MAPPINGS: dict[str, dict[str, Any]] = {
    "submissions": {
        **_common,
        "form": {"type": "keyword"}, "form_kind": {"type": "keyword"}, "form_version": {"type": "integer"},
        "point_id": {"type": "keyword"}, "point_name": {"type": "keyword"}, "area": {"type": "keyword"},
        "area_code": {"type": "keyword"}, "started_at": {"type": "date"}, "ended_at": {"type": "date"},
        "received_at": {"type": "date"}, "duration_s": {"type": "integer"}, "location": {"type": "geo_point"},
        "accuracy_m": {"type": "float"}, "clock_offset_s": {"type": "integer"}, "app_version": {"type": "keyword"},
        "answers": {"type": "flattened"},
    },
    "stops": {
        **_common,
        "date": {"type": "date"}, "started_at": {"type": "date"}, "ended_at": {"type": "date"},
        "dwell_s": {"type": "integer"}, "location": {"type": "geo_point"}, "classification": {"type": "keyword"},
        "point_id": {"type": "keyword"}, "distance_to_point_m": {"type": "float"}, "submissions": {"type": "integer"},
    },
    "day-summaries": {
        **_common,
        "date": {"type": "date"}, "distance_m": {"type": "float"}, "n_stops": {"type": "integer"},
        "n_stops_at_points": {"type": "integer"}, "total_dwell_s": {"type": "integer"},
        "n_submissions": {"type": "integer"}, "assigned": {"type": "integer"}, "completed": {"type": "integer"},
        "n_gaps": {"type": "integer"}, "gap_s": {"type": "integer"}, "risk_score": {"type": "integer"},
        "first_activity": {"type": "date"}, "last_activity": {"type": "date"},
    },
    "flags": {
        **_common,
        "date": {"type": "date"}, "rule": {"type": "keyword"}, "title": {"type": "text"},
        "severity": {"type": "keyword"}, "status": {"type": "keyword"}, "escalated": {"type": "boolean"},
        "created_at": {"type": "date"}, "updated_at": {"type": "date"}, "resolved_at": {"type": "date"},
    },
}  # fmt: skip


def _auth() -> dict[str, Any]:
    s = get_settings()
    if s.es_api_key:
        return {"headers": {"Authorization": f"ApiKey {s.es_api_key}"}}
    if s.es_username and s.es_password:
        return {"auth": (s.es_username, s.es_password)}
    return {}


def _members(db: Session, campaign: Campaign, label: str) -> dict[uuid.UUID, tuple[str, str]]:
    """user id -> (label, team name) for everyone in the campaign's teams."""
    out: dict[uuid.UUID, tuple[str, str]] = {}
    for u, t in db.execute(
        select(User, Team)
        .join(TeamMember, TeamMember.user_id == User.id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(Team.campaign_id == campaign.id)
    ).tuples():
        name = u.full_name if label == "name" else u.username if label == "username" else str(u.id)
        out.setdefault(u.id, (name, t.name))
    return out


def _iso(v: datetime | date | None) -> str | None:
    return v.isoformat() if v else None


def _bulk(es: Any, lines: list[str]) -> int:
    """Send NDJSON lines; raise if any item failed. Returns the number of actions."""
    if not lines:
        return 0
    n = 0
    step = BATCH * 2
    for i in range(0, len(lines), step):
        chunk = lines[i : i + step]
        r = es.post(
            "/_bulk",
            content="\n".join(chunk) + "\n",
            headers={"Content-Type": "application/x-ndjson"},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            first: dict[str, Any] = next(
                (v for it in body.get("items", []) for v in it.values() if v.get("error")), {}
            )
            raise RuntimeError(
                f"Elasticsearch refused documents: {json.dumps(first.get('error'))[:500]}"
            )
        n += sum(1 for line in chunk if line.startswith('{"index"') or line.startswith('{"delete"'))
    return n


def run(
    db: Session, campaign: Campaign, cfg: dict[str, Any], cursor: dict[str, Any], now: datetime
) -> RunResult:
    if not cfg.get("url"):
        raise ConfigError("Set integrations.elasticsearch.url in the campaign config")
    prefix = str(cfg.get("index_prefix") or "ragoh")
    label = str(cfg.get("collector_label") or "name")
    people = _members(db, campaign, label)
    ids = list(people)
    stats: dict[str, Any] = {}
    new_cursor = dict(cursor)

    def base(uid: uuid.UUID) -> dict[str, Any]:
        name, team = people.get(uid, (str(uid), None))
        return {
            "campaign": campaign.name,
            "collector": name,
            "collector_id": str(uid),
            "team": team,
        }

    with client(base_url=str(cfg["url"]).rstrip("/"), **_auth()) as es:
        if cursor.get("templates") != TEMPLATE_VERSION:
            for kind, props in MAPPINGS.items():
                r = es.put(
                    f"/_index_template/{prefix}-{kind}",
                    json={
                        "index_patterns": [f"{prefix}-{kind}"],
                        "template": {"mappings": {"properties": props}},
                    },
                )
                r.raise_for_status()
            new_cursor["templates"] = TEMPLATE_VERSION

        # Submissions received since last time.
        since = datetime.fromisoformat(cursor.get("submissions", EPOCH)) - LOOKBACK
        rows = db.execute(
            select(Submission, Form, FormVersion.version, VisitPoint, AdminArea)
            .join(FormVersion, FormVersion.id == Submission.form_version_id)
            .join(Form, Form.id == FormVersion.form_id)
            .outerjoin(VisitPoint, VisitPoint.id == Submission.visit_point_id)
            .outerjoin(AdminArea, AdminArea.id == VisitPoint.admin_area_id)
            .where(Form.campaign_id == campaign.id, Submission.received_at >= since)
            .order_by(Submission.received_at)
        ).tuples().all()  # fmt: skip
        lines: list[str] = []
        latest = since
        for s, form, ver, vp, area in rows:
            g = to_shape(s.end_geom) if s.end_geom is not None else None
            doc = {
                **base(s.user_id), "form": form.name, "form_kind": form.kind.value, "form_version": ver,
                "point_id": vp.external_id if vp else None, "point_name": vp.name if vp else None,
                "area": area.name if area else None, "area_code": area.code if area else None,
                "started_at": _iso(s.started_at), "ended_at": _iso(s.ended_at), "received_at": _iso(s.received_at),
                "duration_s": s.duration_s, "location": {"lat": g.y, "lon": g.x} if g else None,
                "accuracy_m": s.accuracy_m, "clock_offset_s": s.clock_offset_s, "app_version": s.app_version,
                "answers": {k: v if isinstance(v, (str, int, float, bool)) else json.dumps(v) for k, v in s.data.items()},
            }  # fmt: skip
            lines += [
                json.dumps({"index": {"_index": f"{prefix}-submissions", "_id": str(s.id)}}),
                json.dumps(doc),
            ]
            latest = max(latest, s.received_at)
        stats["submissions"] = _bulk(es, lines)
        new_cursor["submissions"] = latest.isoformat()

        # Day summaries (and their stops) recomputed since last time.
        since = datetime.fromisoformat(cursor.get("days", EPOCH)) - LOOKBACK
        days = list(
            db.scalars(
                select(CollectorDaySummary)
                .where(CollectorDaySummary.user_id.in_(ids), CollectorDaySummary.computed_at >= since)
                .order_by(CollectorDaySummary.computed_at)
            )
        ) if ids else []  # fmt: skip
        lines = []
        latest = since
        n_stops = 0
        for d in days:
            day_id = f"{d.user_id}_{d.local_date.isoformat()}"
            lines += [
                json.dumps({"index": {"_index": f"{prefix}-day-summaries", "_id": day_id}}),
                json.dumps({
                    **base(d.user_id), "date": d.local_date.isoformat(), "distance_m": d.distance_m,
                    "n_stops": d.n_stops, "n_stops_at_points": d.n_stops_at_points, "total_dwell_s": d.total_dwell_s,
                    "n_submissions": d.n_submissions, "assigned": d.assigned, "completed": d.completed,
                    "n_gaps": d.n_gaps, "gap_s": d.gap_s, "risk_score": d.risk_score,
                    "first_activity": _iso(d.first_activity), "last_activity": _iso(d.last_activity),
                }),
            ]  # fmt: skip
            latest = max(latest, d.computed_at)
            # Replace that day's stops: delete the old documents, index the current ones.
            r = es.post(
                f"/{prefix}-stops/_delete_by_query?conflicts=proceed&ignore_unavailable=true",
                json={"query": {"bool": {"filter": [
                    {"term": {"collector_id": str(d.user_id)}}, {"term": {"date": d.local_date.isoformat()}},
                ]}}},
            )  # fmt: skip
            if r.status_code != 404:
                r.raise_for_status()
            for st, ext in db.execute(
                select(Stop, VisitPoint.external_id)
                .outerjoin(VisitPoint, VisitPoint.id == Stop.visit_point_id)
                .where(Stop.user_id == d.user_id, Stop.local_date == d.local_date)
            ).tuples():
                c = to_shape(st.centroid)
                lines += [
                    json.dumps({"index": {"_index": f"{prefix}-stops", "_id": str(st.id)}}),
                    json.dumps({
                        **base(st.user_id), "date": st.local_date.isoformat(), "started_at": _iso(st.started_at),
                        "ended_at": _iso(st.ended_at), "dwell_s": st.dwell_s, "location": {"lat": c.y, "lon": c.x},
                        "classification": st.classification, "point_id": ext,
                        "distance_to_point_m": st.distance_to_point_m, "submissions": len(st.submission_ids),
                    }),
                ]  # fmt: skip
                n_stops += 1
        _bulk(es, lines)
        stats["day_summaries"], stats["stops"] = len(days), n_stops
        new_cursor["days"] = latest.isoformat()

        # Flags created or changed since last time.
        since = datetime.fromisoformat(cursor.get("flags", EPOCH)) - LOOKBACK
        flags = list(
            db.scalars(
                select(Flag)
                .where(Flag.campaign_id == campaign.id, Flag.updated_at >= since)
                .order_by(Flag.updated_at)
            )
        )
        lines = []
        latest = since
        for f in flags:
            lines += [
                json.dumps({"index": {"_index": f"{prefix}-flags", "_id": str(f.id)}}),
                json.dumps({
                    **base(f.collector_id), "date": f.local_date.isoformat(), "rule": f.rule_id, "title": f.title,
                    "severity": f.severity.value, "status": f.status.value, "escalated": f.escalated,
                    "created_at": _iso(f.created_at), "updated_at": _iso(f.updated_at), "resolved_at": _iso(f.resolved_at),
                }),
            ]  # fmt: skip
            latest = max(latest, f.updated_at)
        stats["flags"] = _bulk(es, lines)
        new_cursor["flags"] = latest.isoformat()
    return RunResult(stats=stats, cursor=new_cursor)
