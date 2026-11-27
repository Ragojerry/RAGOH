import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdminArea, AreaLevel
from app.services.geo import multipolygon_from_geojson


def create_or_update_area(
    db: Session,
    *,
    code: str,
    name: str,
    level: AreaLevel,
    parent: AdminArea | None,
    geometry: dict[str, Any] | None,
) -> tuple[AdminArea, bool]:
    """Upsert an admin area by code. Returns (area, created)."""
    area = db.scalar(select(AdminArea).where(AdminArea.code == code))
    created = area is None
    if area is None:
        area_id = uuid.uuid4()
        area = AdminArea(id=area_id, code=code)
        db.add(area)
    else:
        area_id = area.id
    area.name = name
    area.level = level
    area.parent_id = parent.id if parent else None
    area.path = f"{parent.path if parent else '/'}{area_id}/"
    if geometry is not None:
        area.geom = multipolygon_from_geojson(geometry)
    db.flush()
    return area, created
