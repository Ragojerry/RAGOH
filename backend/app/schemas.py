"""Request and response models."""

import uuid
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.models import AreaLevel, AssignmentStatus, PointType, Role, TeamRole, UserStatus


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Auth -------------------------------------------------------------------
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    device_id: uuid.UUID | None = None


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20)


class UserOut(ORM):
    id: uuid.UUID
    username: str
    full_name: str
    phone: str | None
    email: str | None
    role: Role
    status: UserStatus
    language: str
    last_login_at: datetime | None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    user: UserOut


# --- Users ------------------------------------------------------------------
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.\-]+$")
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    role: Role
    phone: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None
    language: str = "en"
    area_ids: list[uuid.UUID] = []


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None
    status: UserStatus | None = None
    language: str | None = None
    area_ids: list[uuid.UUID] | None = None


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)


# --- Areas ------------------------------------------------------------------
class AdminAreaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=64)
    level: AreaLevel
    parent_id: uuid.UUID | None = None
    geometry: dict[str, Any] | None = Field(
        default=None, description="GeoJSON Polygon or MultiPolygon (WGS84)"
    )


class AdminAreaOut(ORM):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    level: AreaLevel
    name: str
    code: str


class ImportReport(BaseModel):
    created: int = 0
    updated: int = 0
    assignments_created: int = 0
    errors: list[dict[str, Any]] = []


# --- Campaigns --------------------------------------------------------------
class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = "survey"
    start_date: date | None = None
    end_date: date | None = None
    work_start_time: time | None = None
    work_end_time: time | None = None
    timezone: str = "Africa/Lagos"
    config: dict[str, Any] = {}


class CampaignOut(ORM):
    id: uuid.UUID
    name: str
    type: str
    start_date: date | None
    end_date: date | None
    work_start_time: time | None
    work_end_time: time | None
    timezone: str
    is_active: bool


class CampaignConfigOut(BaseModel):
    campaign_id: uuid.UUID
    overrides: dict[str, Any]
    effective: dict[str, Any]


# --- Teams ------------------------------------------------------------------
class TeamCreate(BaseModel):
    campaign_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    admin_area_id: uuid.UUID


class TeamMemberIn(BaseModel):
    user_id: uuid.UUID
    role_in_team: TeamRole


class TeamMemberOut(ORM):
    id: uuid.UUID
    user_id: uuid.UUID
    role_in_team: TeamRole
    active_from: date
    active_to: date | None


class TeamOut(ORM):
    id: uuid.UUID
    campaign_id: uuid.UUID
    name: str
    admin_area_id: uuid.UUID
    members: list[TeamMemberOut] = []


# --- Visit points & assignments --------------------------------------------
class VisitPointOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    external_id: str
    name: str
    type: PointType
    admin_area_id: uuid.UUID | None
    lat: float
    lon: float
    geofence_m: int
    priority: int
    window_start: time | None
    window_end: time | None
    attributes: dict[str, Any]


class VisitPointPage(BaseModel):
    total: int
    items: list[VisitPointOut]


class AssignmentOut(BaseModel):
    id: uuid.UUID
    collector_id: uuid.UUID
    planned_date: date | None
    status: AssignmentStatus
    visit_point: VisitPointOut
