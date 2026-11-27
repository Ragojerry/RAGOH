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
    kind: str = "visit"
    note: str | None = None


# --- Forms ------------------------------------------------------------------
class FormVersionOut(BaseModel):
    form_id: uuid.UUID
    version_id: uuid.UUID
    campaign_id: uuid.UUID
    id_string: str
    name: str
    kind: str
    version: str
    published_at: datetime
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


# --- Sync -------------------------------------------------------------------
class GeoPointIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class DeviceIn(BaseModel):
    id: uuid.UUID
    model: str | None = Field(default=None, max_length=120)
    android_version: str | None = Field(default=None, max_length=20)
    app_version: str | None = Field(default=None, max_length=20)


class ShiftIn(BaseModel):
    id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = Field(default=None, max_length=30)
    start_location: GeoPointIn | None = None
    end_location: GeoPointIn | None = None


class SubmissionIn(BaseModel):
    id: uuid.UUID
    form_version_id: uuid.UUID
    shift_id: uuid.UUID | None = None
    assignment_id: uuid.UUID | None = None
    visit_point_id: uuid.UUID | None = None
    started_at: datetime
    ended_at: datetime
    start_location: GeoPointIn | None = None
    end_location: GeoPointIn | None = None
    data: dict[str, Any]


class AssignmentUpdateIn(BaseModel):
    id: uuid.UUID = Field(description="Client UUID of this status change (for idempotency)")
    assignment_id: uuid.UUID
    status: AssignmentStatus
    reason: str | None = Field(default=None, max_length=500)
    changed_at: datetime


class GpsPointIn(BaseModel):
    id: uuid.UUID
    recorded_at: datetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    altitude_m: float | None = None
    speed_mps: float | None = None
    bearing: float | None = None
    battery_pct: int | None = Field(default=None, ge=0, le=100)
    is_mock: bool = False
    provider: str | None = Field(default=None, max_length=20)
    network_type: str | None = Field(default=None, max_length=20)
    shift_id: uuid.UUID | None = None


class DeviceEventIn(BaseModel):
    id: uuid.UUID
    type: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")
    occurred_at: datetime
    shift_id: uuid.UUID | None = None
    details: dict[str, Any] = {}


class PhoneMessageIn(BaseModel):
    id: uuid.UUID
    recipient_id: uuid.UUID
    body: str = Field(min_length=1, max_length=2000)
    sent_at: datetime


class FlagReplyIn(BaseModel):
    id: uuid.UUID
    flag_id: uuid.UUID
    comment: str = Field(min_length=1, max_length=2000)
    sent_at: datetime


class RelayIn(BaseModel):
    """A collector's records, sealed on the collector's phone and carried by a supervisor.

    ciphertext = ChaCha20-Poly1305(collector relay key, nonce, zlib(JSON SyncPushIn)),
    with the collector's id (16 raw bytes) as associated data.
    """

    id: uuid.UUID
    collector_id: uuid.UUID
    nonce: str = Field(min_length=16, max_length=24)  # base64 of 12 bytes
    ciphertext: str = Field(min_length=24, max_length=1_500_000)
    scanned_at: datetime = Field(description="Supervisor's phone time when it was received")


class SyncPushIn(BaseModel):
    device: DeviceIn
    device_time: datetime
    shifts: list[ShiftIn] = []
    submissions: list[SubmissionIn] = []
    assignment_updates: list[AssignmentUpdateIn] = []
    gps_points: list[GpsPointIn] = []
    device_events: list[DeviceEventIn] = []
    messages: list[PhoneMessageIn] = []
    flag_replies: list[FlagReplyIn] = []
    relays: list[RelayIn] = Field(default=[], max_length=20)


class RejectedItem(BaseModel):
    kind: str
    id: uuid.UUID
    error: str


class SyncPushOut(BaseModel):
    server_time: datetime
    accepted: dict[str, list[uuid.UUID]]
    rejected: list[RejectedItem]


class CampaignSyncOut(BaseModel):
    id: uuid.UUID
    name: str
    config: dict[str, Any]


class SyncMessageOut(BaseModel):
    id: uuid.UUID
    sender_id: uuid.UUID
    sender_name: str
    recipient_id: uuid.UUID | None
    flag_id: uuid.UUID | None
    body: str
    sent_at: datetime


class SyncFlagOut(BaseModel):
    """A flag the supervisor asked this collector to explain."""

    id: uuid.UUID
    title: str
    local_date: date
    status: str
    details: dict[str, Any]


class SyncContactOut(BaseModel):
    id: uuid.UUID
    full_name: str
    phone: str | None


class TeamMemberSyncOut(BaseModel):
    id: uuid.UUID
    username: str
    full_name: str
    phone: str | None
    team_id: uuid.UUID
    team_name: str


class SyncPullOut(BaseModel):
    server_time: datetime
    user: UserOut
    campaigns: list[CampaignSyncOut]
    forms: list[FormVersionOut]
    assignments: list[AssignmentOut]
    messages: list[SyncMessageOut] = []
    my_flags: list[SyncFlagOut] = []
    supervisors: list[SyncContactOut] = []
    team_members: list[TeamMemberSyncOut] = Field(
        default=[], description="Collectors this user supervises (supervisors only)"
    )


class MediaOut(BaseModel):
    submission_id: uuid.UUID
    field_name: str
    size_bytes: int


# --- Collector day (tracking results) ----------------------------------------
class StopOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    ended_at: datetime
    dwell_s: int
    lat: float
    lon: float
    n_points: int
    classification: str
    visit_point_id: uuid.UUID | None
    visit_point_name: str | None
    distance_to_point_m: float | None
    submission_ids: list[str]


class DaySummaryOut(ORM):
    local_date: date
    distance_m: float
    n_gps_points: int
    n_mock_points: int
    n_stops: int
    n_stops_at_points: int
    total_dwell_s: int
    median_dwell_s: int
    moving_s: int
    stationary_s: int
    n_gaps: int
    gap_s: int
    first_activity: datetime | None
    last_activity: datetime | None
    assigned: int
    completed: int
    n_submissions: int
    computed_at: datetime


class DaySubmissionOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    ended_at: datetime
    duration_s: int
    visit_point_id: uuid.UUID | None
    lat: float | None
    lon: float | None


class DeviceEventOut(BaseModel):
    type: str
    occurred_at: datetime
    details: dict[str, Any]


class PlannedRouteOut(BaseModel):
    created_at: datetime
    source: str
    distance_m: float
    duration_s: int
    stop_order: list[str] = Field(description="assignment ids in the suggested order")
    geometry: list[list[float]] = Field(description="[lon, lat]")


class CollectorDayOut(BaseModel):
    user: UserOut
    date: date
    summary: DaySummaryOut | None
    stops: list[StopOut]
    submissions: list[DaySubmissionOut]
    events: list[DeviceEventOut]
    track: list[list[float]] = Field(description="[lon, lat, unix_seconds] simplified to ~10 m")
    planned: PlannedRouteOut | None = Field(
        default=None, description="Latest route plan the collector made that day"
    )


class ConsentIn(BaseModel):
    notice_version: str = Field(min_length=1, max_length=20)
