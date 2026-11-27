"""Flags inbox, supervisor follow-up actions, risk list, messages and back-checks."""

import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.defaults import merged_config
from app.core.deps import DB, CurrentUser, StaffUser
from app.db.models import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
    Backcheck,
    BackcheckStatus,
    CollectorDaySummary,
    Flag,
    FlagAction,
    FlagStatus,
    Message,
    Role,
    Severity,
    Submission,
    Team,
    TeamMember,
    TeamRole,
    User,
    VisitPoint,
)
from app.db.session import get_session_factory
from app.services.audit import audit
from app.services.flags import update_risk
from app.services.push import deliver_pending, notify
from app.services.scope import supervised_team_ids, visible_user_ids_condition
from app.services.tracking import SessionFactory, user_campaign

router = APIRouter(tags=["flags"])

ActionName = Literal[
    "acknowledge", "note", "call", "message", "request_explanation", "revisit",
    "backcheck", "escalate", "resolve", "dismiss", "reopen", "reply",
]  # fmt: skip
NEEDS_COMMENT = {"note", "message", "request_explanation", "resolve", "dismiss", "reply", "revisit"}


class FlagActionOut(BaseModel):
    id: uuid.UUID
    action: str
    comment: str | None
    data: dict[str, Any]
    actor_id: uuid.UUID | None
    actor_name: str | None
    created_at: datetime


class FlagOut(BaseModel):
    id: uuid.UUID
    rule_id: str
    title: str
    severity: Severity
    status: FlagStatus
    escalated: bool
    collector_id: uuid.UUID
    collector_name: str
    local_date: date
    subject_type: str
    subject_id: str
    details: dict[str, Any]
    resolution_comment: str | None
    created_at: datetime
    updated_at: datetime
    actions: list[FlagActionOut] | None = None


class FlagPage(BaseModel):
    total: int
    items: list[FlagOut]


class ActionIn(BaseModel):
    action: ActionName
    comment: str | None = Field(default=None, max_length=2000)
    assignee_id: uuid.UUID | None = Field(
        default=None, description="backcheck: who does the re-interview"
    )
    visit_point_id: uuid.UUID | None = Field(
        default=None, description="backcheck/revisit when the flag isn't about one point"
    )
    phone: str | None = Field(default=None, max_length=32, description="call: number dialled")


class RiskRow(BaseModel):
    collector_id: uuid.UUID
    collector_name: str
    username: str
    local_date: date
    risk_score: int
    risk_breakdown: dict[str, Any]
    open_flags: int
    completed: int
    n_submissions: int
    n_stops_at_points: int
    distance_m: float


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    recipient_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = Field(
        default=None, description="Send to the whole team (broadcast)"
    )


class MessageOut(BaseModel):
    id: uuid.UUID
    sender_id: uuid.UUID
    sender_name: str
    recipient_id: uuid.UUID | None
    team_id: uuid.UUID | None
    flag_id: uuid.UUID | None
    body: str
    sent_at: datetime
    read_at: datetime | None


class BackcheckOut(BaseModel):
    id: uuid.UUID
    visit_point_id: uuid.UUID
    point_name: str
    original_collector_id: uuid.UUID
    assigned_to: uuid.UUID
    status: BackcheckStatus
    flag_id: uuid.UUID | None
    match: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None


# ---------------------------------------------------------------------------
def _names(db: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    clean = [i for i in ids if i]
    if not clean:
        return {}
    return dict(
        db.execute(select(User.id, User.full_name).where(User.id.in_(clean))).tuples().all()
    )


def _flag_out(
    db: Session, f: Flag, names: dict[uuid.UUID, str], with_actions: bool = False
) -> FlagOut:
    out = FlagOut(
        id=f.id, rule_id=f.rule_id, title=f.title, severity=f.severity, status=f.status, escalated=f.escalated,
        collector_id=f.collector_id, collector_name=names.get(f.collector_id, ""), local_date=f.local_date,
        subject_type=f.subject_type, subject_id=f.subject_id, details=f.details, resolution_comment=f.resolution_comment,
        created_at=f.created_at, updated_at=f.updated_at,
    )  # fmt: skip
    if with_actions:
        acts = list(
            db.scalars(
                select(FlagAction).where(FlagAction.flag_id == f.id).order_by(FlagAction.created_at)
            )
        )
        an = _names(db, {a.actor_id for a in acts})
        out.actions = [
            FlagActionOut(
                id=a.id,
                action=a.action,
                comment=a.comment,
                data=a.data,
                actor_id=a.actor_id,
                actor_name=an.get(a.actor_id) if a.actor_id else None,
                created_at=a.created_at,
            )  # fmt: skip
            for a in acts
        ]
    return out


def _visible_flag(db: Session, actor: User, flag_id: uuid.UUID) -> Flag:
    f = db.get(Flag, flag_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found")
    if actor.role == Role.collector:
        if f.collector_id != actor.id or not f.collector_visible:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found")
        return f
    if not db.scalar(
        select(User.id).where(User.id == f.collector_id, visible_user_ids_condition(db, actor))
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found")
    return f


def _point_for(db: Session, f: Flag, override: uuid.UUID | None) -> VisitPoint | None:
    if override:
        return db.get(VisitPoint, override)
    if f.subject_type == "assignment":
        a = db.get(Assignment, uuid.UUID(f.subject_id))
        return db.get(VisitPoint, a.visit_point_id) if a else None
    if f.subject_type == "submission":
        s = db.get(Submission, uuid.UUID(f.subject_id))
        return db.get(VisitPoint, s.visit_point_id) if s and s.visit_point_id else None
    return None


def _same_team(db: Session, a: uuid.UUID, b: uuid.UUID) -> bool:
    teams_a = select(TeamMember.team_id).where(
        TeamMember.user_id == a, TeamMember.active_to.is_(None)
    )
    return (
        db.scalar(
            select(TeamMember.id)
            .where(
                TeamMember.user_id == b,
                TeamMember.active_to.is_(None),
                TeamMember.team_id.in_(teams_a),
            )
            .limit(1)
        )
        is not None
    )


# ---------------------------------------------------------------------------
@router.get("/flags", response_model=FlagPage)
def list_flags(
    db: DB,
    actor: CurrentUser,
    status_: list[FlagStatus] | None = Query(None, alias="status"),
    severity: list[Severity] | None = Query(None),
    rule_id: str | None = None,
    collector_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    escalated: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> FlagPage:
    stmt = select(Flag)
    if actor.role == Role.collector:
        stmt = stmt.where(Flag.collector_id == actor.id, Flag.collector_visible.is_(True))
    else:
        stmt = stmt.where(
            Flag.collector_id.in_(select(User.id).where(visible_user_ids_condition(db, actor)))
        )
    if status_:
        stmt = stmt.where(Flag.status.in_(status_))
    if severity:
        stmt = stmt.where(Flag.severity.in_(severity))
    if rule_id:
        stmt = stmt.where(Flag.rule_id == rule_id)
    if collector_id:
        stmt = stmt.where(Flag.collector_id == collector_id)
    if date_from:
        stmt = stmt.where(Flag.local_date >= date_from)
    if date_to:
        stmt = stmt.where(Flag.local_date <= date_to)
    if escalated is not None:
        stmt = stmt.where(Flag.escalated.is_(escalated))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    sev_order = case(
        (Flag.severity == Severity.high, 0), (Flag.severity == Severity.medium, 1), else_=2
    )
    rows = list(
        db.scalars(
            stmt.order_by(Flag.local_date.desc(), sev_order, Flag.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    names = _names(db, {f.collector_id for f in rows})
    return FlagPage(total=total, items=[_flag_out(db, f, names) for f in rows])


@router.get("/flags/{flag_id}", response_model=FlagOut)
def get_flag(flag_id: uuid.UUID, db: DB, actor: CurrentUser) -> FlagOut:
    f = _visible_flag(db, actor, flag_id)
    return _flag_out(db, f, _names(db, {f.collector_id}), with_actions=True)


@router.post("/flags/{flag_id}/actions", response_model=FlagOut)
def act_on_flag(
    flag_id: uuid.UUID,
    body: ActionIn,
    db: DB,
    actor: CurrentUser,
    background: BackgroundTasks,
    factory: SessionFactory = Depends(get_session_factory),
) -> FlagOut:
    f = _visible_flag(db, actor, flag_id)
    is_collector = actor.role == Role.collector
    if is_collector != (body.action == "reply"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Collectors can only reply; supervisors can't reply on a collector's behalf",
        )
    if body.action in NEEDS_COMMENT and not (body.comment and body.comment.strip()):
        raise HTTPException(422, f"A comment is required to {body.action.replace('_', ' ')}")
    if f.status in (FlagStatus.resolved, FlagStatus.dismissed) and body.action not in (
        "reopen",
        "note",
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "This flag is closed. Reopen it first.")

    data: dict[str, Any] = {}
    now = datetime.now(UTC)

    def bump(to: FlagStatus) -> None:
        order = [FlagStatus.new, FlagStatus.acknowledged, FlagStatus.under_review]
        if f.status in order and order.index(to) > order.index(f.status):
            f.status = to

    match body.action:
        case "acknowledge" | "note":
            bump(FlagStatus.acknowledged)
        case "call":
            data["phone"] = body.phone
            bump(FlagStatus.acknowledged)
        case "message" | "request_explanation":
            msg = Message(
                sender_id=actor.id,
                recipient_id=f.collector_id,
                flag_id=f.id,
                body=body.comment or "",
            )
            db.add(msg)
            db.flush()
            data["message_id"] = str(msg.id)
            if body.action == "request_explanation":
                f.collector_visible = True
                bump(FlagStatus.under_review)
            else:
                bump(FlagStatus.acknowledged)
        case "revisit":
            point = _point_for(db, f, body.visit_point_id)
            a = (
                db.scalar(
                    select(Assignment).where(
                        Assignment.visit_point_id == point.id,
                        Assignment.collector_id == f.collector_id,
                    )
                )
                if point
                else None
            )
            if a is None:
                raise HTTPException(
                    422, "No visit of this collector to send back to — choose a point"
                )
            a.status, a.status_reason, a.status_changed_at, a.note = (
                AssignmentStatus.pending,
                "Revisit requested",
                now,
                body.comment,
            )
            db.add(
                Message(
                    sender_id=actor.id,
                    recipient_id=f.collector_id,
                    flag_id=f.id,
                    body=f"Please revisit {point.name if point else 'the point'}: {body.comment}",
                )
            )
            data["assignment_id"] = str(a.id)
            bump(FlagStatus.under_review)
        case "backcheck":
            point = _point_for(db, f, body.visit_point_id)
            if point is None:
                raise HTTPException(422, "Choose the visit point to back-check")
            assignee = db.get(User, body.assignee_id or actor.id)
            if assignee is None or assignee.id == f.collector_id:
                raise HTTPException(
                    422,
                    "A back-check must be done by someone other than the collector being checked",
                )
            if assignee.id != actor.id and not _same_team(db, assignee.id, f.collector_id):
                raise HTTPException(422, "The back-check assignee must be in the collector's team")
            original = db.scalar(
                select(Submission)
                .where(Submission.visit_point_id == point.id, Submission.user_id == f.collector_id)
                .order_by(Submission.ended_at.desc())
            )
            a = Assignment(
                visit_point_id=point.id, collector_id=assignee.id, kind=AssignmentKind.backcheck,
                assigned_by=actor.id, note=body.comment or "Back-check requested",
            )  # fmt: skip
            db.add(a)
            db.flush()
            bc = Backcheck(
                visit_point_id=point.id, original_submission_id=original.id if original else None, original_collector_id=f.collector_id,
                assigned_to=assignee.id, requested_by=actor.id, flag_id=f.id, assignment_id=a.id,
            )  # fmt: skip
            db.add(bc)
            db.flush()
            data.update(
                {"backcheck_id": str(bc.id), "assignee_id": str(assignee.id), "point": point.name}
            )
            if assignee.id != actor.id:
                notify(
                    db, assignee.id, "backcheck", "New back-check", f"Back-check {point.name}",
                    {"assignment_id": str(a.id)}, dedupe_key=f"backcheck:{bc.id}",
                )  # fmt: skip
            bump(FlagStatus.under_review)
        case "escalate":
            f.escalated = True
            bump(FlagStatus.under_review)
        case "resolve" | "dismiss":
            f.status = FlagStatus.resolved if body.action == "resolve" else FlagStatus.dismissed
            f.resolution_comment, f.resolved_by, f.resolved_at = body.comment, actor.id, now
        case "reopen":
            f.status, f.resolved_at, f.resolved_by = FlagStatus.acknowledged, None, None
        case "reply":
            last_request = db.scalar(
                select(FlagAction)
                .where(FlagAction.flag_id == f.id, FlagAction.action == "request_explanation")
                .order_by(FlagAction.created_at.desc())
            )
            if last_request and last_request.actor_id:
                db.add(
                    Message(
                        sender_id=actor.id,
                        recipient_id=last_request.actor_id,
                        flag_id=f.id,
                        body=body.comment or "",
                    )
                )

    db.add(
        FlagAction(
            flag_id=f.id, actor_id=actor.id, action=body.action, comment=body.comment, data=data
        )
    )
    if body.action in ("resolve", "dismiss", "reopen"):
        c = user_campaign(db, f.collector_id)
        update_risk(db, f.collector_id, f.local_date, merged_config(c.config if c else None))
    audit(db, f"flag_{body.action}", actor_id=actor.id, entity="flag", entity_id=f.id)
    db.commit()
    background.add_task(deliver_pending, factory)
    db.refresh(f)
    return _flag_out(db, f, _names(db, {f.collector_id}), with_actions=True)


@router.get("/risk", response_model=list[RiskRow])
def risk_list(db: DB, actor: StaffUser, day: date = Query(alias="date")) -> list[RiskRow]:
    """Collectors in scope for a day, highest risk first."""
    collectors = list(
        db.scalars(
            select(User)
            .where(User.role == Role.collector, visible_user_ids_condition(db, actor))
            .order_by(User.full_name)
        )
    )
    ids = [u.id for u in collectors]
    summaries = {
        s.user_id: s
        for s in db.scalars(
            select(CollectorDaySummary).where(
                CollectorDaySummary.user_id.in_(ids), CollectorDaySummary.local_date == day
            )
        )
    }
    open_counts = dict(
        db.execute(
            select(Flag.collector_id, func.count())
            .where(
                Flag.collector_id.in_(ids),
                Flag.local_date == day,
                Flag.status.in_([FlagStatus.new, FlagStatus.acknowledged, FlagStatus.under_review]),
            )
            .group_by(Flag.collector_id)
        )
        .tuples()
        .all()
    )
    rows = []
    for u in collectors:
        s = summaries.get(u.id)
        rows.append(
            RiskRow(
                collector_id=u.id,
                collector_name=u.full_name,
                username=u.username,
                local_date=day,
                risk_score=s.risk_score if s else 0,
                risk_breakdown=s.risk_breakdown if s else {},
                open_flags=open_counts.get(u.id, 0),
                completed=s.completed if s else 0,
                n_submissions=s.n_submissions if s else 0,
                n_stops_at_points=s.n_stops_at_points if s else 0,
                distance_m=s.distance_m if s else 0.0,
            )  # fmt: skip
        )
    rows.sort(key=lambda r: (-r.risk_score, -r.open_flags, r.collector_name))
    return rows


# --- Messages ------------------------------------------------------------------
def my_team_ids(db: Session, user: User) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(TeamMember.team_id).where(
                TeamMember.user_id == user.id, TeamMember.active_to.is_(None)
            )
        )
    )


def messages_for(db: Session, user: User, since: datetime | None = None) -> list[Message]:
    cond = or_(
        Message.recipient_id == user.id,
        Message.sender_id == user.id,
        and_(Message.team_id.in_(my_team_ids(db, user)), Message.recipient_id.is_(None)),
    )
    stmt = select(Message).where(cond)
    if since:
        stmt = stmt.where(Message.sent_at >= since)
    return list(db.scalars(stmt.order_by(Message.sent_at.desc()).limit(200)))


def message_out(m: Message, names: dict[uuid.UUID, str]) -> MessageOut:
    return MessageOut(
        id=m.id, sender_id=m.sender_id, sender_name=names.get(m.sender_id, ""), recipient_id=m.recipient_id, team_id=m.team_id,
        flag_id=m.flag_id, body=m.body, sent_at=m.sent_at, read_at=m.read_at,
    )  # fmt: skip


@router.get("/messages", response_model=list[MessageOut])
def list_messages(db: DB, user: CurrentUser) -> list[MessageOut]:
    msgs = messages_for(db, user)
    names = _names(db, {m.sender_id for m in msgs})
    return [message_out(m, names) for m in msgs]


@router.post("/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
def send_message(
    body: MessageIn,
    db: DB,
    user: CurrentUser,
    background: BackgroundTasks,
    factory: SessionFactory = Depends(get_session_factory),
) -> MessageOut:
    if (body.recipient_id is None) == (body.team_id is None):
        raise HTTPException(422, "Send to one person (recipient_id) or one team (team_id)")
    if body.team_id is not None:
        if user.role == Role.collector or (
            user.role == Role.supervisor and body.team_id not in supervised_team_ids(db, user)
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "You can only broadcast to teams you supervise"
            )
        if db.get(Team, body.team_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    else:
        assert body.recipient_id is not None
        allowed = (
            db.scalar(
                select(User.id).where(
                    User.id == body.recipient_id, visible_user_ids_condition(db, user)
                )
            )
            if user.role != Role.collector
            else _supervises(db, body.recipient_id, user.id)
        )
        if not allowed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipient not found")
    m = Message(
        sender_id=user.id, recipient_id=body.recipient_id, team_id=body.team_id, body=body.body
    )
    db.add(m)
    db.commit()
    background.add_task(deliver_pending, factory)
    return message_out(m, {user.id: user.full_name})


def _supervises(db: Session, supervisor_id: uuid.UUID, collector_id: uuid.UUID) -> bool:
    sup_teams = select(TeamMember.team_id).where(
        TeamMember.user_id == supervisor_id,
        TeamMember.role_in_team == TeamRole.supervisor,
        TeamMember.active_to.is_(None),
    )
    return (
        db.scalar(
            select(TeamMember.id)
            .where(
                TeamMember.user_id == collector_id,
                TeamMember.active_to.is_(None),
                TeamMember.team_id.in_(sup_teams),
            )
            .limit(1)
        )
        is not None
    )


@router.post("/messages/{message_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(message_id: uuid.UUID, db: DB, user: CurrentUser) -> None:
    m = db.get(Message, message_id)
    if m is None or m.recipient_id not in (user.id, None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    if m.recipient_id == user.id and m.read_at is None:
        m.read_at = datetime.now(UTC)
        db.commit()


# --- Back-checks -----------------------------------------------------------------
@router.get("/backchecks", response_model=list[BackcheckOut])
def list_backchecks(
    db: DB, actor: StaffUser, status_: BackcheckStatus | None = Query(None, alias="status")
) -> list[BackcheckOut]:
    visible = select(User.id).where(visible_user_ids_condition(db, actor))
    stmt = (
        select(Backcheck, VisitPoint.name)
        .join(VisitPoint, VisitPoint.id == Backcheck.visit_point_id)
        .where(Backcheck.original_collector_id.in_(visible))
    )
    if status_:
        stmt = stmt.where(Backcheck.status == status_)
    return [
        BackcheckOut(
            id=b.id,
            visit_point_id=b.visit_point_id,
            point_name=name,
            original_collector_id=b.original_collector_id,
            assigned_to=b.assigned_to,
            status=b.status,
            flag_id=b.flag_id,
            match=b.match,
            created_at=b.created_at,
            completed_at=b.completed_at,
        )  # fmt: skip
        for b, name in db.execute(stmt.order_by(Backcheck.created_at.desc()))
    ]
