import uuid
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select

from app.core.deps import DB, CurrentUser, ManagerOrAdmin, StaffUser
from app.db.models import AdminArea, Assignment, Campaign, PointType, Role, User, VisitPoint
from app.schemas import AssignmentOut, ImportReport, VisitPointOut, VisitPointPage
from app.services.audit import audit
from app.services.geo import lonlat
from app.services.imports import ImportFileError, import_visit_points, parse_rows
from app.services.scope import area_filter

router = APIRouter(tags=["visit points"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def point_out(p: VisitPoint) -> VisitPointOut:
    lon, lat = lonlat(p.geom)
    return VisitPointOut(
        id=p.id,
        campaign_id=p.campaign_id,
        external_id=p.external_id,
        name=p.name,
        type=p.type,
        admin_area_id=p.admin_area_id,
        lat=lat,
        lon=lon,
        geofence_m=p.geofence_m,
        priority=p.priority,
        window_start=p.window_start,
        window_end=p.window_end,
        attributes=p.attributes or {},
    )


def _scoped_points(db: DB, user: User):  # type: ignore[no-untyped-def]
    """Visit points the user may see. Points with no area are visible to admins only."""
    stmt = select(VisitPoint).where(VisitPoint.deleted_at.is_(None))
    if user.role == Role.admin:
        return stmt
    return stmt.join(AdminArea, AdminArea.id == VisitPoint.admin_area_id).where(
        area_filter(db, user, AdminArea.path)
    )


@router.get("/visit-points", response_model=VisitPointPage)
def list_points(
    db: DB,
    user: StaffUser,
    campaign_id: uuid.UUID | None = None,
    admin_area_id: uuid.UUID | None = None,
    type: PointType | None = None,  # noqa: A002
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> VisitPointPage:
    stmt = _scoped_points(db, user)
    if campaign_id:
        stmt = stmt.where(VisitPoint.campaign_id == campaign_id)
    if admin_area_id:
        area = db.get(AdminArea, admin_area_id)
        if area is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Area not found")
        sub = select(AdminArea.id).where(AdminArea.path.startswith(area.path, autoescape=True))
        stmt = stmt.where(VisitPoint.admin_area_id.in_(sub))
    if type:
        stmt = stmt.where(VisitPoint.type == type)
    if bbox:
        try:
            minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
        except ValueError:
            raise HTTPException(422, "bbox must be minLon,minLat,maxLon,maxLat") from None
        stmt = stmt.where(
            VisitPoint.geom.intersects(func.ST_MakeEnvelope(minx, miny, maxx, maxy, 4326))
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(VisitPoint.external_id).limit(limit).offset(offset))
    return VisitPointPage(total=total, items=[point_out(p) for p in rows])


@router.get("/visit-points/{point_id}", response_model=VisitPointOut)
def get_point(point_id: uuid.UUID, db: DB, user: StaffUser) -> VisitPointOut:
    p = db.scalar(_scoped_points(db, user).where(VisitPoint.id == point_id))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visit point not found")
    return point_out(p)


@router.post("/visit-points/import", response_model=ImportReport)
async def import_points(
    db: DB,
    user: ManagerOrAdmin,
    file: UploadFile,
    campaign_id: uuid.UUID = Form(...),
    dry_run: bool = Form(False),
) -> ImportReport:
    """Upload CSV, XLSX or GeoJSON. With dry_run=true nothing is saved; you get the report only."""
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or campaign.organisation_id != user.organisation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File larger than 20 MB")
    try:
        rows = parse_rows(file.filename or "", content)
        report = import_visit_points(db, campaign, rows, user)
    except ImportFileError as exc:
        raise HTTPException(422, str(exc)) from None

    if dry_run:
        db.rollback()
        return report
    audit(
        db,
        "visit_point_import",
        actor_id=user.id,
        entity="campaign",
        entity_id=campaign.id,
        details={
            "file": file.filename,
            **report.model_dump(exclude={"errors"}),
            "errors": len(report.errors),
        },
    )
    db.commit()
    return report


@router.get("/me/assignments", response_model=list[AssignmentOut])
def my_assignments(
    db: DB, user: CurrentUser, planned_date: date | None = None
) -> list[AssignmentOut]:
    stmt = (
        select(Assignment, VisitPoint)
        .join(VisitPoint, VisitPoint.id == Assignment.visit_point_id)
        .where(Assignment.collector_id == user.id, VisitPoint.deleted_at.is_(None))
        .order_by(VisitPoint.priority.desc(), VisitPoint.external_id)
    )
    if planned_date is not None:
        stmt = stmt.where(
            (Assignment.planned_date == planned_date) | Assignment.planned_date.is_(None)
        )
    return [
        AssignmentOut(
            id=a.id,
            collector_id=a.collector_id,
            planned_date=a.planned_date,
            status=a.status,
            visit_point=point_out(p),
        )
        for a, p in db.execute(stmt)
    ]
