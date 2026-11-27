"""Database models for Phase 1 (organisation, people, geography, visit points, auth, audit).

Later phases add tracking, forms, flags and messaging tables (see docs/ARCHITECTURE.md §6).
All primary keys are UUIDs so records created offline on phones can be merged safely.
"""

import enum
import uuid
from datetime import date, datetime, time
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class Role(enum.StrEnum):
    collector = "collector"
    supervisor = "supervisor"
    manager = "manager"
    admin = "admin"


class UserStatus(enum.StrEnum):
    active = "active"
    disabled = "disabled"


class AreaLevel(enum.StrEnum):
    country = "country"
    state = "state"
    lga = "lga"
    ward = "ward"
    settlement = "settlement"


class PointType(enum.StrEnum):
    household = "household"
    settlement = "settlement"
    facility = "facility"
    school = "school"
    other = "other"


class AssignmentStatus(enum.StrEnum):
    pending = "pending"
    en_route = "en_route"
    arrived = "arrived"
    in_progress = "in_progress"
    completed = "completed"
    not_found = "not_found"
    refused = "refused"
    skipped = "skipped"


class TeamRole(enum.StrEnum):
    collector = "collector"
    supervisor = "supervisor"


# ---------------------------------------------------------------------------
# Organisation & geography
# ---------------------------------------------------------------------------
class Organisation(TimestampMixin, Base):
    __tablename__ = "organisations"
    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(String(200), unique=True)


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"
    id: Mapped[uuid.UUID] = _uuid()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(50), default="survey")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    work_start_time: Mapped[time | None] = mapped_column(Time)
    work_end_time: Mapped[time | None] = mapped_column(Time)
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Lagos")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AdminArea(TimestampMixin, Base):
    """State → LGA → ward → settlement hierarchy.

    `path` is a materialised path of ancestor ids ("/<root>/<child>/.../<self>/") so that
    "is this area inside the user's scope" is a cheap prefix match.
    """

    __tablename__ = "admin_areas"
    id: Mapped[uuid.UUID] = _uuid()
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_areas.id"))
    level: Mapped[AreaLevel] = mapped_column(_enum(AreaLevel, "area_level"))
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(64), unique=True)
    path: Mapped[str] = mapped_column(Text, index=True)
    geom: Mapped[Any | None] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=True)
    )

    __table_args__ = (
        Index("ix_admin_areas_path_pattern", "path", postgresql_ops={"path": "text_pattern_ops"}),
    )


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid()
    organisation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.id"))
    username: Mapped[str] = mapped_column(String(80), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    pin_hash: Mapped[str | None] = mapped_column(Text)
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"))
    status: Mapped[UserStatus] = mapped_column(
        _enum(UserStatus, "user_status"), default=UserStatus.active
    )
    language: Mapped[str] = mapped_column(String(8), default="en")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    area_access: Mapped[list["UserAreaAccess"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserAreaAccess(Base):
    __tablename__ = "user_area_access"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    admin_area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_areas.id"), primary_key=True)
    user: Mapped[User] = relationship(back_populates="area_access")


class Team(TimestampMixin, Base):
    __tablename__ = "teams"
    id: Mapped[uuid.UUID] = _uuid()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    name: Mapped[str] = mapped_column(String(200))
    admin_area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_areas.id"))
    __table_args__ = (UniqueConstraint("campaign_id", "name"),)


class TeamMember(Base):
    __tablename__ = "team_members"
    id: Mapped[uuid.UUID] = _uuid()
    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    role_in_team: Mapped[TeamRole] = mapped_column(_enum(TeamRole, "team_role"))
    active_from: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    active_to: Mapped[date | None] = mapped_column(Date)


class Device(TimestampMixin, Base):
    __tablename__ = "devices"
    id: Mapped[uuid.UUID] = _uuid()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    model: Mapped[str | None] = mapped_column(String(120))
    android_version: Mapped[str | None] = mapped_column(String(20))
    app_version: Mapped[str | None] = mapped_column(String(20))
    push_token: Mapped[str | None] = mapped_column(Text)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[uuid.UUID] = _uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    notice_version: Mapped[str] = mapped_column(String(20))
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


# ---------------------------------------------------------------------------
# Work
# ---------------------------------------------------------------------------
class VisitPoint(TimestampMixin, Base):
    __tablename__ = "visit_points"
    id: Mapped[uuid.UUID] = _uuid()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[PointType] = mapped_column(_enum(PointType, "point_type"))
    admin_area_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_areas.id"), index=True
    )
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    geofence_m: Mapped[int] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[time | None] = mapped_column(Time)
    window_end: Mapped[time | None] = mapped_column(Time)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("campaign_id", "external_id"),)


class Assignment(TimestampMixin, Base):
    __tablename__ = "assignments"
    id: Mapped[uuid.UUID] = _uuid()
    visit_point_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visit_points.id"), index=True)
    collector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    planned_date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[AssignmentStatus] = mapped_column(
        _enum(AssignmentStatus, "assignment_status"), default=AssignmentStatus.pending
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


# ---------------------------------------------------------------------------
# Forms, shifts and submissions (Phase 2)
# ---------------------------------------------------------------------------
class FormKind(enum.StrEnum):
    survey = "survey"
    supervision = "supervision"
    backcheck = "backcheck"


class Form(TimestampMixin, Base):
    __tablename__ = "forms"
    id: Mapped[uuid.UUID] = _uuid()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    id_string: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[FormKind] = mapped_column(_enum(FormKind, "form_kind"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("campaign_id", "id_string"),)


class FormVersion(Base):
    __tablename__ = "form_versions"
    id: Mapped[uuid.UUID] = _uuid()
    form_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("forms.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(50))
    schema: Mapped[dict[str, Any]] = mapped_column(JSONB)
    xlsform: Mapped[bytes] = mapped_column(LargeBinary)
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (UniqueConstraint("form_id", "version"),)


class Shift(Base):
    """A collector's working period. Created on the phone (client UUID), synced later."""

    __tablename__ = "shifts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(30))
    start_geom: Mapped[Any | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False)
    )
    end_geom: Mapped[Any | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    form_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("form_versions.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id"))
    shift_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assignments.id"), index=True
    )
    visit_point_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("visit_points.id"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_s: Mapped[int] = mapped_column(Integer)
    start_geom: Mapped[Any | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False)
    )
    end_geom: Mapped[Any | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    app_version: Mapped[str | None] = mapped_column(String(20))
    clock_offset_s: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SubmissionMedia(Base):
    __tablename__ = "submission_media"
    id: Mapped[uuid.UUID] = _uuid()
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    field_name: Mapped[str] = mapped_column(String(100))
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (UniqueConstraint("submission_id", "field_name"),)


# ---------------------------------------------------------------------------
# Tracking (Phase 3)
# ---------------------------------------------------------------------------
class GpsPoint(Base):
    """Raw GPS fixes recorded during shifts. Partitioned by month on recorded_at.

    The primary key includes recorded_at (required for partitioning); phones resend the
    same (id, recorded_at) on retries, so inserts use ON CONFLICT DO NOTHING.
    """

    __tablename__ = "gps_points"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    shift_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    altitude_m: Mapped[float | None] = mapped_column(Float)
    speed_mps: Mapped[float | None] = mapped_column(Float)
    bearing: Mapped[float | None] = mapped_column(Float)
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str | None] = mapped_column(String(20))
    network_type: Mapped[str | None] = mapped_column(String(20))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_gps_points_user_time", "user_id", "recorded_at"),
        {"postgresql_partition_by": "RANGE (recorded_at)"},
    )


class DeviceEvent(Base):
    """Things the phone noticed: GPS switched off, mock location app, tracking restarted…"""

    __tablename__ = "device_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    shift_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    type: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Stop(Base):
    """A place where a collector stayed (computed from GPS; recomputed when new points arrive)."""

    __tablename__ = "stops"
    id: Mapped[uuid.UUID] = _uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    local_date: Mapped[date] = mapped_column(Date, index=True)
    shift_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dwell_s: Mapped[int] = mapped_column(Integer)
    centroid: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    n_points: Mapped[int] = mapped_column(Integer)
    visit_point_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visit_points.id"))
    distance_to_point_m: Mapped[float | None] = mapped_column(Float)
    classification: Mapped[str] = mapped_column(String(30))
    submission_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)


class CollectorDaySummary(Base):
    __tablename__ = "collector_day_summaries"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    distance_m: Mapped[float] = mapped_column(Float, default=0)
    n_gps_points: Mapped[int] = mapped_column(Integer, default=0)
    n_mock_points: Mapped[int] = mapped_column(Integer, default=0)
    n_stops: Mapped[int] = mapped_column(Integer, default=0)
    n_stops_at_points: Mapped[int] = mapped_column(Integer, default=0)
    total_dwell_s: Mapped[int] = mapped_column(Integer, default=0)
    median_dwell_s: Mapped[int] = mapped_column(Integer, default=0)
    moving_s: Mapped[int] = mapped_column(Integer, default=0)
    stationary_s: Mapped[int] = mapped_column(Integer, default=0)
    n_gaps: Mapped[int] = mapped_column(Integer, default=0)
    gap_s: Mapped[int] = mapped_column(Integer, default=0)
    first_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    n_submissions: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
class AuditLog(Base):
    """Append-only record of security-relevant actions."""

    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = _uuid()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AppConfig(Base):
    __tablename__ = "app_config"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
