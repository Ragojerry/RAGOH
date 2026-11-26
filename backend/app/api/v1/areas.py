import json
import uuid

from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy import select

from app.core.deps import DB, AdminUser, CurrentUser
from app.db.models import AdminArea, AreaLevel
from app.schemas import AdminAreaCreate, AdminAreaOut, ImportReport
from app.services.areas import create_or_update_area
from app.services.audit import audit
from app.services.scope import area_filter

router = APIRouter(prefix="/admin-areas", tags=["admin areas"])

LEVEL_ORDER = [
    AreaLevel.country,
    AreaLevel.state,
    AreaLevel.lga,
    AreaLevel.ward,
    AreaLevel.settlement,
]


@router.get("", response_model=list[AdminAreaOut])
def list_areas(
    db: DB,
    user: CurrentUser,
    level: AreaLevel | None = None,
    parent_id: uuid.UUID | None = None,
) -> list[AdminArea]:
    stmt = select(AdminArea).where(area_filter(db, user, AdminArea.path)).order_by(AdminArea.name)
    if level is not None:
        stmt = stmt.where(AdminArea.level == level)
    if parent_id is not None:
        stmt = stmt.where(AdminArea.parent_id == parent_id)
    return list(db.scalars(stmt))


@router.post("", response_model=AdminAreaOut, status_code=status.HTTP_201_CREATED)
def create_area(body: AdminAreaCreate, db: DB, admin: AdminUser) -> AdminArea:
    parent = db.get(AdminArea, body.parent_id) if body.parent_id else None
    if body.parent_id and parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent area not found")
    try:
        area, _ = create_or_update_area(
            db,
            code=body.code,
            name=body.name,
            level=body.level,
            parent=parent,
            geometry=body.geometry,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"Invalid geometry: {exc}") from None
    audit(db, "admin_area_upsert", actor_id=admin.id, entity="admin_area", entity_id=area.id)
    db.commit()
    return area


@router.post("/import", response_model=ImportReport)
async def import_areas(file: UploadFile, db: DB, admin: AdminUser) -> ImportReport:
    """GeoJSON FeatureCollection; each feature's properties need `code`, `name`, `level`
    and optionally `parent_code`. Parents may appear anywhere in the file."""
    try:
        data = json.loads(await file.read())
        features = data["features"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "Expected a GeoJSON FeatureCollection") from None

    report = ImportReport()

    def level_rank(f: dict) -> int:  # type: ignore[type-arg]
        try:
            return LEVEL_ORDER.index(
                AreaLevel(str(f.get("properties", {}).get("level", "")).lower())
            )
        except ValueError:
            return 99

    for idx, f in sorted(enumerate(features, start=1), key=lambda t: level_rank(t[1])):
        props = f.get("properties") or {}
        try:
            code = str(props["code"]).strip()
            name = str(props["name"]).strip()
            level = AreaLevel(str(props["level"]).strip().lower())
            parent = None
            if props.get("parent_code"):
                parent = db.scalar(
                    select(AdminArea).where(AdminArea.code == str(props["parent_code"]))
                )
                if parent is None:
                    raise ValueError(f"parent_code '{props['parent_code']}' not found")
            _, created = create_or_update_area(
                db, code=code, name=name, level=level, parent=parent, geometry=f.get("geometry")
            )
            report.created += int(created)
            report.updated += int(not created)
        except (KeyError, ValueError, TypeError) as exc:
            report.errors.append({"feature": idx, "code": props.get("code"), "error": str(exc)})

    audit(db, "admin_area_import", actor_id=admin.id, details=report.model_dump(exclude={"errors"}))
    db.commit()
    return report
