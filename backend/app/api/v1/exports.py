"""Exports: submissions, stops, flags and points as CSV/XLSX; GPS tracks as GeoJSON.

Scoped like the dashboard (supervisor → own teams, manager → own geography) and filtered
by date range, team and area. Every export is written to the audit log (who, what, which
filters, how many rows) because tracking data is personal data.
"""

import csv
import io
import json
import uuid
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from geoalchemy2.shape import to_shape
from openpyxl import Workbook
from shapely.geometry import LineString
from sqlalchemy import select

from app.api.v1.analytics import _range, team_collectors
from app.core.deps import DB, StaffUser
from app.db.models import (
    AdminArea,
    Assignment,
    AssignmentKind,
    Flag,
    Form,
    FormVersion,
    GpsPoint,
    Stop,
    Submission,
    Team,
    TeamMember,
    TeamRole,
    User,
    VisitPoint,
)
from app.services.audit import audit
from app.services.scope import area_filter
from app.services.tracking import DEFAULT_TZ

router = APIRouter(tags=["exports"])

MAX_ROWS = 200_000
TRACK_SIMPLIFY_DEG = 0.0001
TZ = ZoneInfo(DEFAULT_TZ)
Kind = Literal["submissions", "stops", "flags", "points", "tracks"]


def _safe(v: Any) -> Any:
    """Plain values for a cell; neutralise spreadsheet formulas (CSV injection)."""
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _table(
    name: str, header: list[str], rows: Iterable[list[Any]], fmt: str
) -> tuple[bytes, str, str]:
    if fmt == "xlsx":
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(name)
        ws.append(header)
        for r in rows:
            ws.append([_safe(v) for v in r])
        buf = io.BytesIO()
        wb.save(buf)
        return (
            buf.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx",
        )
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for r in rows:
        w.writerow([_safe(v) for v in r])
    # BOM so Excel opens UTF-8 names (Hausa/Yoruba diacritics) correctly.
    return ("﻿" + out.getvalue()).encode(), "text/csv; charset=utf-8", "csv"


@router.get("/exports/{kind}")
def export(
    kind: Kind,
    db: DB,
    actor: StaffUser,
    date_from: date,
    date_to: date,
    format: Literal["csv", "xlsx", "geojson"] = Query("csv"),  # noqa: A002
    team_id: uuid.UUID | None = None,
    area_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
) -> Response:
    start, end = _range(date_from, date_to)
    if (kind == "tracks") != (format == "geojson"):
        raise HTTPException(422, "Tracks export as geojson; other exports as csv or xlsx")
    members = team_collectors(db, actor, team_id)
    area = None
    if area_id is not None:
        area = db.scalar(
            select(AdminArea).where(AdminArea.id == area_id, area_filter(db, actor, AdminArea.path))
        )
        if area is None:
            raise HTTPException(404, "Area not found")
        team_areas = {
            tid: path
            for tid, path in db.execute(
                select(Team.id, AdminArea.path).join(AdminArea, AdminArea.id == Team.admin_area_id)
            )
        }
        members = {
            uid: (u, t) for uid, (u, t) in members.items() if team_areas[t.id].startswith(area.path)
        }
    ids = list(members)
    name_of = {uid: u.full_name for uid, (u, _) in members.items()}
    team_of = {uid: t.name for uid, (_, t) in members.items()}

    body: bytes
    media: str
    ext: str
    n = 0
    if kind == "submissions":
        # Include the teams' supervisors too: their spot checks are part of the data.
        team_ids = list({t.id for _, t in members.values()})
        for u, t in db.execute(
            select(User, Team)
            .join(TeamMember, TeamMember.user_id == User.id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(
                TeamMember.team_id.in_(team_ids),
                TeamMember.role_in_team == TeamRole.supervisor,
                TeamMember.active_to.is_(None),
            )
        ).tuples():
            if u.id not in name_of:
                ids.append(u.id)
                name_of[u.id], team_of[u.id] = u.full_name, t.name
        stmt = (
            select(Submission, Form.name, FormVersion.version, VisitPoint.external_id, VisitPoint.name)
            .join(FormVersion, FormVersion.id == Submission.form_version_id)
            .join(Form, Form.id == FormVersion.form_id)
            .outerjoin(VisitPoint, VisitPoint.id == Submission.visit_point_id)
            .where(Submission.user_id.in_(ids), Submission.ended_at >= start, Submission.ended_at < end)
            .order_by(Submission.ended_at)
            .limit(MAX_ROWS)
        )  # fmt: skip
        if campaign_id:
            stmt = stmt.where(Form.campaign_id == campaign_id)
        rows = db.execute(stmt).tuples().all()
        fields: list[str] = []
        seen: set[str] = set()
        for s, *_ in rows:
            for k in s.data:
                if k not in seen:
                    seen.add(k)
                    fields.append(k)
        header = [
            "submission_id", "form", "form_version", "collector", "team", "point_id", "point_name",
            "started_at", "ended_at", "duration_s", "lat", "lon", "accuracy_m", "clock_offset_s",
            "app_version",
        ] + fields  # fmt: skip

        def sub_rows() -> Iterable[list[Any]]:
            for s, form, ver, ext_id, pname in rows:
                g = to_shape(s.end_geom) if s.end_geom is not None else None
                yield [
                    s.id, form, ver, name_of.get(s.user_id), team_of.get(s.user_id), ext_id, pname,
                    s.started_at, s.ended_at, s.duration_s, g.y if g else None, g.x if g else None,
                    s.accuracy_m, s.clock_offset_s, s.app_version,
                ] + [s.data.get(f) for f in fields]  # fmt: skip

        n = len(rows)
        body, media, ext = _table("submissions", header, sub_rows(), format)
    elif kind == "stops":
        rows2 = db.execute(
            select(Stop, VisitPoint.external_id)
            .outerjoin(VisitPoint, VisitPoint.id == Stop.visit_point_id)
            .where(Stop.user_id.in_(ids), Stop.local_date >= date_from, Stop.local_date <= date_to)
            .order_by(Stop.user_id, Stop.started_at)
            .limit(MAX_ROWS)
        ).tuples().all()  # fmt: skip
        header = [
            "stop_id", "collector", "team", "date", "started_at", "ended_at", "dwell_s", "lat", "lon",
            "gps_points", "classification", "point_id", "distance_to_point_m", "submissions",
        ]  # fmt: skip

        def stop_rows() -> Iterable[list[Any]]:
            for st, ext_id in rows2:
                c = to_shape(st.centroid)
                yield [
                    st.id, name_of.get(st.user_id), team_of.get(st.user_id), st.local_date, st.started_at,
                    st.ended_at, st.dwell_s, round(c.y, 6), round(c.x, 6), st.n_points, st.classification,
                    ext_id, st.distance_to_point_m, len(st.submission_ids),
                ]  # fmt: skip

        n = len(rows2)
        body, media, ext = _table("stops", header, stop_rows(), format)
    elif kind == "flags":
        flags = list(
            db.scalars(
                select(Flag)
                .where(
                    Flag.collector_id.in_(ids),
                    Flag.local_date >= date_from,
                    Flag.local_date <= date_to,
                )
                .order_by(Flag.local_date, Flag.collector_id)
                .limit(MAX_ROWS)
            )
        )
        resolvers = {
            u.id: u.full_name
            for u in db.scalars(select(User).where(User.id.in_({f.resolved_by for f in flags if f.resolved_by})))
        }  # fmt: skip
        header = [
            "flag_id", "date", "collector", "team", "rule", "title", "severity", "status", "escalated",
            "subject_type", "subject_id", "details", "created_at", "resolved_at", "resolved_by",
            "resolution_comment",
        ]  # fmt: skip
        n = len(flags)
        body, media, ext = _table(
            "flags",
            header,
            (
                [
                    f.id,
                    f.local_date,
                    name_of.get(f.collector_id),
                    team_of.get(f.collector_id),
                    f.rule_id,
                    f.title,
                    f.severity.value,
                    f.status.value,
                    f.escalated,
                    f.subject_type,
                    f.subject_id,
                    f.details,
                    f.created_at,
                    f.resolved_at,
                    resolvers.get(f.resolved_by) if f.resolved_by else None,
                    f.resolution_comment,
                ]  # fmt: skip
                for f in flags
            ),
            format,
        )
    elif kind == "points":
        stmt3 = (
            select(VisitPoint, AdminArea.name, Assignment.status, Assignment.collector_id, Assignment.status_changed_at)
            .join(AdminArea, AdminArea.id == VisitPoint.admin_area_id)
            .outerjoin(Assignment, (Assignment.visit_point_id == VisitPoint.id) & (Assignment.kind == AssignmentKind.visit))
            .where(VisitPoint.deleted_at.is_(None), area_filter(db, actor, AdminArea.path))
            .order_by(VisitPoint.external_id)
            .limit(MAX_ROWS)
        )  # fmt: skip
        if area is not None:
            stmt3 = stmt3.where(AdminArea.path.startswith(area.path, autoescape=True))
        if campaign_id:
            stmt3 = stmt3.where(VisitPoint.campaign_id == campaign_id)
        if team_id is not None or actor.role.value == "supervisor":
            stmt3 = stmt3.where(Assignment.collector_id.in_(ids))
        rows3 = db.execute(stmt3).tuples().all()
        header = [
            "point_id", "name", "type", "area", "lat", "lon", "priority", "status", "collector", "team",
            "status_changed_at",
        ]  # fmt: skip

        def point_rows() -> Iterable[list[Any]]:
            for vp, area_name, st, cid, changed in rows3:
                g = to_shape(vp.geom)
                yield [
                    vp.external_id, vp.name, vp.type.value, area_name, round(g.y, 6), round(g.x, 6),
                    vp.priority, st.value if st else "unassigned", name_of.get(cid) if cid else None,
                    team_of.get(cid) if cid else None, changed,
                ]  # fmt: skip

        n = len(rows3)
        body, media, ext = _table("points", header, point_rows(), format)
    else:  # tracks
        if (date_to - date_from).days > 31:
            raise HTTPException(422, "Export tracks for at most 31 days at a time")
        features = []
        rows4 = db.execute(
            select(GpsPoint.user_id, GpsPoint.recorded_at, GpsPoint.geom)
            .where(
                GpsPoint.user_id.in_(ids),
                GpsPoint.recorded_at >= start,
                GpsPoint.recorded_at < end,
                GpsPoint.is_mock.is_(False),
            )
            .order_by(GpsPoint.user_id, GpsPoint.recorded_at)
        ).tuples()
        by_day: dict[tuple[uuid.UUID, date], list[tuple[float, float]]] = {}
        for uid, t, g in rows4:
            p = to_shape(g)
            by_day.setdefault((uid, t.astimezone(TZ).date()), []).append((p.x, p.y))
            n += 1
        for (uid, d), pts in by_day.items():
            if len(pts) < 2:
                continue
            line = LineString(pts).simplify(TRACK_SIMPLIFY_DEG)
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "collector": name_of.get(uid),
                        "team": team_of.get(uid),
                        "collector_id": str(uid),
                        "date": d.isoformat(),
                        "gps_points": len(pts),
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[round(x, 6), round(y, 6)] for x, y in line.coords],
                    },
                }  # fmt: skip
            )
        body = json.dumps({"type": "FeatureCollection", "features": features}).encode()
        media, ext = "application/geo+json", "geojson"

    audit(
        db, "export", actor_id=actor.id, entity="export", entity_id=kind,
        details={"format": format, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
                 "team_id": str(team_id) if team_id else None, "area_id": str(area_id) if area_id else None,
                 "rows": n},
    )  # fmt: skip
    db.commit()
    filename = f"ragoh-{kind}-{date_from.isoformat()}-to-{date_to.isoformat()}.{ext}"
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
