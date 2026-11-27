"""Import visit points from CSV, XLSX or GeoJSON.

Columns (case-insensitive; only the first four are required):
    external_id, name, lat, lon, type, admin_area_code, priority, geofence_m,
    window_start (HH:MM), window_end (HH:MM), planned_date (YYYY-MM-DD), collector (username)
Any other column is kept in `attributes`.

Rows are upserted by (campaign, external_id), so re-importing a corrected file is safe.
If admin_area_code is missing, the point is placed in the smallest admin area containing it.
"""

import csv
import io
import json
import uuid
from datetime import date, datetime, time
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.defaults import geofence_for
from app.db.models import (
    AdminArea,
    AreaLevel,
    Assignment,
    Campaign,
    PointType,
    Role,
    User,
    VisitPoint,
)
from app.schemas import ImportReport
from app.services.geo import point_wkt

KNOWN = {
    "external_id", "name", "lat", "lon", "type", "admin_area_code", "priority", "geofence_m",
    "window_start", "window_end", "planned_date", "collector",
}  # fmt: skip
ALIASES = {
    "id": "external_id", "latitude": "lat", "longitude": "lon", "lng": "lon", "long": "lon",
    "area_code": "admin_area_code", "ward_code": "admin_area_code", "date": "planned_date",
    "assigned_to": "collector", "username": "collector",
}  # fmt: skip
LEVEL_DEPTH = {
    AreaLevel.country: 0,
    AreaLevel.state: 1,
    AreaLevel.lga: 2,
    AreaLevel.ward: 3,
    AreaLevel.settlement: 4,
}


class ImportFileError(ValueError):
    pass


def _norm_key(key: str) -> str:
    k = str(key).strip().lower().replace(" ", "_")
    return ALIASES.get(k, k)


def parse_rows(filename: str, content: bytes) -> list[dict[str, Any]]:
    name = filename.lower()
    if name.endswith(".csv"):
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        return [{_norm_key(k): v for k, v in row.items() if k is not None} for row in reader]
    if name.endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows_iter = ws.iter_rows(values_only=True)
        header = next(rows_iter, None)
        if not header:
            raise ImportFileError("The spreadsheet is empty")
        keys = [_norm_key(h) if h is not None else "" for h in header]
        out = []
        for values in rows_iter:
            if values is None or all(v is None or str(v).strip() == "" for v in values):
                continue
            out.append({k: v for k, v in zip(keys, values, strict=False) if k})
        return out
    if name.endswith((".geojson", ".json")):
        data = json.loads(content)
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, list):
            raise ImportFileError("GeoJSON must be a FeatureCollection")
        out = []
        for f in features:
            props = {_norm_key(k): v for k, v in (f.get("properties") or {}).items()}
            geom = f.get("geometry") or {}
            if geom.get("type") == "Point" and len(geom.get("coordinates", [])) >= 2:
                props["lon"], props["lat"] = geom["coordinates"][0], geom["coordinates"][1]
            out.append(props)
        return out
    raise ImportFileError("Unsupported file type. Use .csv, .xlsx or .geojson")


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _parse_time(v: Any) -> time | None:
    if _blank(v):
        return None
    if isinstance(v, time):
        return v
    return time.fromisoformat(str(v).strip())


def _parse_date(v: Any) -> date | None:
    if _blank(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v).strip()[:10])


def _smallest_area_containing(db: Session, lon: float, lat: float) -> AdminArea | None:
    pt = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
    areas = db.scalars(
        select(AdminArea).where(AdminArea.geom.is_not(None), func.ST_Contains(AdminArea.geom, pt))
    ).all()
    if not areas:
        return None
    return max(areas, key=lambda a: LEVEL_DEPTH[a.level])


def import_visit_points(
    db: Session, campaign: Campaign, rows: list[dict[str, Any]], actor: User
) -> ImportReport:
    settings = get_settings()
    report = ImportReport()
    if len(rows) > settings.max_import_rows:
        raise ImportFileError(
            f"Too many rows ({len(rows)}); the limit is {settings.max_import_rows}"
        )

    minx, miny, maxx, maxy = settings.country_bbox
    areas_by_code = {a.code: a for a in db.scalars(select(AdminArea))}
    collectors = {
        u.username.lower(): u for u in db.scalars(select(User).where(User.role == Role.collector))
    }
    existing = {
        p.external_id: p
        for p in db.scalars(select(VisitPoint).where(VisitPoint.campaign_id == campaign.id))
    }
    seen: set[str] = set()

    for i, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            ext = str(row.get("external_id") or "").strip()
            if not ext:
                raise ValueError("external_id is required")
            if ext in seen:
                raise ValueError(f"duplicate external_id '{ext}' in file")
            seen.add(ext)
            name = str(row.get("name") or "").strip() or ext
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("lat and lon must be numbers") from None
            if not (miny <= lat <= maxy and minx <= lon <= maxx):
                raise ValueError(
                    f"coordinates ({lat}, {lon}) are outside Nigeria — are lat and lon swapped?"
                )
            ptype_raw = str(row.get("type") or "household").strip().lower()
            try:
                ptype = PointType(ptype_raw)
            except ValueError:
                raise ValueError(
                    f"type '{ptype_raw}' is not one of {[t.value for t in PointType]}"
                ) from None

            area: AdminArea | None = None
            code = row.get("admin_area_code")
            if not _blank(code):
                area = areas_by_code.get(str(code).strip())
                if area is None:
                    raise ValueError(f"unknown admin_area_code '{code}'")
            else:
                area = _smallest_area_containing(db, lon, lat)

            geofence = (
                int(float(row["geofence_m"]))
                if not _blank(row.get("geofence_m"))
                else geofence_for(ptype.value, campaign.config)
            )
            priority = int(float(row["priority"])) if not _blank(row.get("priority")) else 0
            window_start = _parse_time(row.get("window_start"))
            window_end = _parse_time(row.get("window_end"))
            planned = _parse_date(row.get("planned_date"))
            extra = {k: v for k, v in row.items() if k not in KNOWN and not _blank(v)}
            extra = json.loads(json.dumps(extra, default=str))

            collector: User | None = None
            if not _blank(row.get("collector")):
                collector = collectors.get(str(row["collector"]).strip().lower())
                if collector is None:
                    raise ValueError(f"collector '{row['collector']}' not found")

            point = existing.get(ext)
            if point is None:
                point = VisitPoint(id=uuid.uuid4(), campaign_id=campaign.id, external_id=ext)
                db.add(point)
                existing[ext] = point
                report.created += 1
            else:
                report.updated += 1
            point.name = name
            point.type = ptype
            point.geom = point_wkt(lon, lat)
            point.admin_area_id = area.id if area else None
            point.geofence_m = geofence
            point.priority = priority
            point.window_start = window_start
            point.window_end = window_end
            point.attributes = extra
            point.deleted_at = None

            if collector is not None:
                db.flush()
                exists = db.scalar(
                    select(Assignment.id).where(
                        Assignment.visit_point_id == point.id,
                        Assignment.collector_id == collector.id,
                        Assignment.planned_date.is_(None)
                        if planned is None
                        else Assignment.planned_date == planned,
                    )
                )
                if exists is None:
                    db.add(
                        Assignment(
                            visit_point_id=point.id,
                            collector_id=collector.id,
                            planned_date=planned,
                            assigned_by=actor.id,
                        )
                    )
                    report.assignments_created += 1
        except ValueError as exc:
            report.errors.append(
                {"row": i, "external_id": row.get("external_id"), "error": str(exc)}
            )

    return report
