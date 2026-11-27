"""Integrations admin: status of each connector, run now, and a DHIS2 preview."""

import uuid
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import get_settings
from app.core.defaults import merged_config
from app.core.deps import DB, AdminUser
from app.db.models import AdminArea, AreaLevel, Campaign, IntegrationState
from app.integrations import dhis2
from app.integrations.base import ConfigError, connectors, run_all
from app.services.audit import audit

router = APIRouter(tags=["integrations"])


class ConnectorStatus(BaseModel):
    name: str
    campaign_id: uuid.UUID
    campaign: str
    enabled: bool
    target: str | None
    secret_set: bool
    every_min: int
    last_run_at: datetime | None
    last_ok_at: datetime | None
    last_status: str | None
    last_error: str | None
    last_stats: dict[str, Any]


def _secret_set(name: str, cfg: dict[str, Any]) -> bool:
    s = get_settings()
    if name == "elasticsearch":
        return bool(s.es_api_key or (s.es_username and s.es_password))
    if name == "dhis2":
        return bool(s.dhis2_password)
    return bool(s.webhook_secret) if cfg.get("auth") == "hmac" else bool(s.digit_password)


@router.get("/integrations", response_model=list[ConnectorStatus])
def list_integrations(db: DB, admin: AdminUser) -> list[ConnectorStatus]:
    out = []
    for c in db.scalars(
        select(Campaign)
        .where(Campaign.organisation_id == admin.organisation_id)
        .order_by(Campaign.name)
    ):
        cfg = merged_config(c.config)["integrations"]
        for name in connectors():
            ccfg = cfg.get(name) or {}
            st = db.get(IntegrationState, (name, c.id))
            url = ccfg.get("url")
            out.append(
                ConnectorStatus(
                    name=name,
                    campaign_id=c.id,
                    campaign=c.name,
                    enabled=bool(ccfg.get("enabled")),
                    target=urlparse(str(url)).netloc or str(url) if url else None,
                    secret_set=_secret_set(name, ccfg),
                    every_min=int(ccfg.get("every_min") or 10),
                    last_run_at=st.last_run_at if st else None,
                    last_ok_at=st.last_ok_at if st else None,
                    last_status=st.last_status if st else None,
                    last_error=st.last_error if st else None,
                    last_stats=st.last_stats if st else {},
                )  # fmt: skip
            )
    return out


@router.post("/integrations/{name}/run")
def run_now(name: str, campaign_id: uuid.UUID, db: DB, admin: AdminUser) -> list[dict[str, Any]]:
    if name not in connectors():
        raise HTTPException(404, "Unknown connector")
    c = db.get(Campaign, campaign_id)
    if c is None or c.organisation_id != admin.organisation_id:
        raise HTTPException(404, "Campaign not found")
    report = run_all(db, only=name, campaign_id=campaign_id, force=True)
    if not report:
        raise HTTPException(409, f"{name} is not enabled for this campaign")
    audit(
        db,
        "integration_run",
        actor_id=admin.id,
        entity="integration",
        entity_id=name,
        details={"result": report[0]},
    )
    db.commit()
    return report


class PreviewRow(BaseModel):
    area_code: str
    area: str
    org_unit: str | None
    values: dict[str, float]


@router.get("/integrations/dhis2/preview", response_model=list[PreviewRow])
def dhis2_preview(
    db: DB, admin: AdminUser, campaign_id: uuid.UUID, day: date | None = Query(None, alias="date")
) -> list[PreviewRow]:
    """What would be sent for one day — for the mapped areas, or all LGAs if none are mapped yet."""
    c = db.get(Campaign, campaign_id)
    if c is None or c.organisation_id != admin.organisation_id:
        raise HTTPException(404, "Campaign not found")
    cfg = merged_config(c.config)["integrations"]["dhis2"]
    mapped: dict[str, str] = cfg.get("org_units") or {}
    codes = list(mapped) or list(
        db.scalars(select(AdminArea.code).where(AdminArea.level == AreaLevel.lga))
    )
    d = day or (datetime.now(UTC).astimezone(ZoneInfo(c.timezone)).date())
    try:
        values = dhis2.compute(db, c, codes, [d])
    except ConfigError as e:
        raise HTTPException(422, str(e)) from e
    names = {a.code: a.name for a in db.scalars(select(AdminArea).where(AdminArea.code.in_(codes)))}
    return [
        PreviewRow(
            area_code=code,
            area=names.get(code, code),
            org_unit=mapped.get(code),
            values=values[(code, d)],
        )
        for code in sorted(codes, key=lambda x: names.get(x, x))
        if (code, d) in values
    ]
