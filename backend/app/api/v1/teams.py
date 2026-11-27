import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import DB, ManagerOrAdmin, StaffUser
from app.db.models import AdminArea, Campaign, Role, Team, TeamMember, TeamRole, User
from app.schemas import TeamCreate, TeamMemberIn, TeamMemberOut, TeamOut
from app.services.audit import audit
from app.services.scope import area_filter, can_see_area

router = APIRouter(prefix="/teams", tags=["teams"])


def _team_out(db: DB, team: Team) -> TeamOut:
    members = db.scalars(
        select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.active_to.is_(None))
    )
    out = TeamOut.model_validate(team)
    out.members = [TeamMemberOut.model_validate(m) for m in members]
    return out


def _visible_team(db: DB, actor: User, team_id: uuid.UUID) -> Team:
    team = db.scalar(
        select(Team)
        .join(AdminArea, AdminArea.id == Team.admin_area_id)
        .where(Team.id == team_id, area_filter(db, actor, AdminArea.path))
    )
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
    return team


@router.get("", response_model=list[TeamOut])
def list_teams(db: DB, actor: StaffUser, campaign_id: uuid.UUID | None = None) -> list[TeamOut]:
    stmt = (
        select(Team)
        .join(AdminArea, AdminArea.id == Team.admin_area_id)
        .where(area_filter(db, actor, AdminArea.path))
        .order_by(Team.name)
    )
    if campaign_id is not None:
        stmt = stmt.where(Team.campaign_id == campaign_id)
    return [_team_out(db, t) for t in db.scalars(stmt)]


@router.post("", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
def create_team(body: TeamCreate, db: DB, actor: ManagerOrAdmin) -> TeamOut:
    campaign = db.get(Campaign, body.campaign_id)
    area = db.get(AdminArea, body.admin_area_id)
    if campaign is None or campaign.organisation_id != actor.organisation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    if area is None or not can_see_area(db, actor, area):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Area not found")
    team = Team(campaign_id=campaign.id, name=body.name, admin_area_id=area.id)
    db.add(team)
    db.flush()
    audit(db, "team_create", actor_id=actor.id, entity="team", entity_id=team.id)
    db.commit()
    return _team_out(db, team)


@router.get("/{team_id}", response_model=TeamOut)
def get_team(team_id: uuid.UUID, db: DB, actor: StaffUser) -> TeamOut:
    return _team_out(db, _visible_team(db, actor, team_id))


@router.post(
    "/{team_id}/members", response_model=TeamMemberOut, status_code=status.HTTP_201_CREATED
)
def add_member(team_id: uuid.UUID, body: TeamMemberIn, db: DB, actor: ManagerOrAdmin) -> TeamMember:
    team = _visible_team(db, actor, team_id)
    user = db.get(User, body.user_id)
    if user is None or user.organisation_id != actor.organisation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    expected = Role.supervisor if body.role_in_team == TeamRole.supervisor else Role.collector
    if user.role != expected:
        raise HTTPException(
            422,
            f"User role is {user.role.value}; a team {body.role_in_team.value} "
            f"must have role {expected.value}",
        )
    existing = db.scalar(
        select(TeamMember).where(
            TeamMember.team_id == team.id,
            TeamMember.user_id == user.id,
            TeamMember.active_to.is_(None),
        )
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "User is already in this team")
    member = TeamMember(team_id=team.id, user_id=user.id, role_in_team=body.role_in_team)
    db.add(member)
    db.flush()
    audit(
        db,
        "team_member_add",
        actor_id=actor.id,
        entity="team",
        entity_id=team.id,
        details={"user_id": str(user.id)},
    )
    db.commit()
    db.refresh(member)
    return member


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(team_id: uuid.UUID, user_id: uuid.UUID, db: DB, actor: ManagerOrAdmin) -> None:
    team = _visible_team(db, actor, team_id)
    member = db.scalar(
        select(TeamMember).where(
            TeamMember.team_id == team.id,
            TeamMember.user_id == user_id,
            TeamMember.active_to.is_(None),
        )
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    member.active_to = date.today()  # keep history; active_to is exclusive, so membership ends now
    audit(
        db,
        "team_member_remove",
        actor_id=actor.id,
        entity="team",
        entity_id=team.id,
        details={"user_id": str(user_id)},
    )
    db.commit()
