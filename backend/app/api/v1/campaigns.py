import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.defaults import merged_config
from app.core.deps import DB, AdminUser, CurrentUser, ManagerOrAdmin
from app.db.models import Campaign, Organisation, Role
from app.schemas import CampaignConfigOut, CampaignCreate, CampaignOut
from app.services.audit import audit

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("", response_model=list[CampaignOut])
def list_campaigns(db: DB, user: CurrentUser) -> list[Campaign]:
    return list(
        db.scalars(
            select(Campaign)
            .where(Campaign.organisation_id == user.organisation_id)
            .order_by(Campaign.created_at.desc())
        )
    )


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(body: CampaignCreate, db: DB, admin: AdminUser) -> Campaign:
    org = db.get(Organisation, admin.organisation_id)
    assert org is not None
    campaign = Campaign(organisation_id=org.id, **body.model_dump())
    db.add(campaign)
    db.flush()
    audit(db, "campaign_create", actor_id=admin.id, entity="campaign", entity_id=campaign.id)
    db.commit()
    return campaign


def _get(db: DB, campaign_id: uuid.UUID, org_id: uuid.UUID) -> Campaign:
    c = db.get(Campaign, campaign_id)
    if c is None or c.organisation_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    return c


@router.get("/{campaign_id}/config", response_model=CampaignConfigOut)
def get_config(campaign_id: uuid.UUID, db: DB, user: CurrentUser) -> CampaignConfigOut:
    c = _get(db, campaign_id, user.organisation_id)
    overrides, effective = dict(c.config or {}), merged_config(c.config)
    if user.role != Role.admin:  # where data is sent is for admins only
        overrides.pop("integrations", None)
        effective.pop("integrations", None)
    return CampaignConfigOut(campaign_id=c.id, overrides=overrides, effective=effective)


@router.put("/{campaign_id}/config", response_model=CampaignConfigOut)
def put_config(
    campaign_id: uuid.UUID, overrides: dict[str, Any], db: DB, user: ManagerOrAdmin
) -> CampaignConfigOut:
    c = _get(db, campaign_id, user.organisation_id)
    if user.role != Role.admin:
        # Integrations decide where personal data is sent: only admins may change them.
        current = (c.config or {}).get("integrations")
        if overrides.get("integrations", current) != current:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can change integrations")
        if current is not None:
            overrides = {**overrides, "integrations": current}
    c.config = overrides
    audit(
        db,
        "campaign_config_update",
        actor_id=user.id,
        entity="campaign",
        entity_id=c.id,
        details=overrides,
    )
    db.commit()
    return CampaignConfigOut(
        campaign_id=c.id, overrides=c.config, effective=merged_config(c.config)
    )
