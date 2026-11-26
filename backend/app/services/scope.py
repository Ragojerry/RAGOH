"""Geographic and team scope.

Every list/read endpoint filters through these helpers so a user only ever receives rows
inside their scope. This is enforced on the server, never just hidden in the apps.

- admin:      everything
- manager:    areas granted in user_area_access (and everything below them)
- supervisor: the areas of teams they supervise, and the members of those teams
- collector:  themselves and their own assignments
"""

import uuid
from datetime import date

from sqlalchemy import ColumnElement, false, or_, select, true
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.db.models import AdminArea, Role, Team, TeamMember, TeamRole, User, UserAreaAccess


def _active_membership(today: date | None = None) -> ColumnElement[bool]:
    today = today or date.today()
    return (TeamMember.active_from <= today) & (
        (TeamMember.active_to.is_(None)) | (TeamMember.active_to > today)
    )


def supervised_team_ids(db: Session, user: User) -> list[uuid.UUID]:
    rows = db.scalars(
        select(TeamMember.team_id).where(
            TeamMember.user_id == user.id,
            TeamMember.role_in_team == TeamRole.supervisor,
            _active_membership(),
        )
    )
    return list(rows)


def allowed_area_paths(db: Session, user: User) -> list[str] | None:
    """Materialised paths the user may see. None means unrestricted (admin)."""
    if user.role == Role.admin:
        return None
    if user.role == Role.manager:
        stmt = (
            select(AdminArea.path)
            .join(UserAreaAccess, UserAreaAccess.admin_area_id == AdminArea.id)
            .where(UserAreaAccess.user_id == user.id)
        )
        return list(db.scalars(stmt))
    if user.role == Role.supervisor:
        team_ids = supervised_team_ids(db, user)
        if not team_ids:
            return []
        stmt = (
            select(AdminArea.path)
            .join(Team, Team.admin_area_id == AdminArea.id)
            .where(Team.id.in_(team_ids))
        )
        return list(db.scalars(stmt))
    return []


def area_filter(
    db: Session, user: User, area_path_col: "ColumnElement[str] | InstrumentedAttribute[str]"
) -> ColumnElement[bool]:
    """SQL condition: the row's area path lies within one of the user's areas."""
    paths = allowed_area_paths(db, user)
    if paths is None:
        return true()
    if not paths:
        return false()
    return or_(*[area_path_col.startswith(p, autoescape=True) for p in paths])


def visible_user_ids_condition(db: Session, user: User) -> ColumnElement[bool]:
    """SQL condition on User.id for the users this user may see."""
    if user.role == Role.admin:
        return true()
    if user.role == Role.collector:
        return User.id == user.id

    if user.role == Role.supervisor:
        team_ids = supervised_team_ids(db, user)
        member_ids = select(TeamMember.user_id).where(
            TeamMember.team_id.in_(team_ids), _active_membership()
        )
        return or_(User.id == user.id, User.id.in_(member_ids))

    # manager: members of teams in their areas, plus users granted areas inside their scope
    paths = allowed_area_paths(db, user) or []
    if not paths:
        return User.id == user.id
    in_scope = or_(*[AdminArea.path.startswith(p, autoescape=True) for p in paths])
    team_members = (
        select(TeamMember.user_id)
        .join(Team, Team.id == TeamMember.team_id)
        .join(AdminArea, AdminArea.id == Team.admin_area_id)
        .where(in_scope)
    )
    area_users = (
        select(UserAreaAccess.user_id)
        .join(AdminArea, AdminArea.id == UserAreaAccess.admin_area_id)
        .where(in_scope)
    )
    return or_(User.id == user.id, User.id.in_(team_members), User.id.in_(area_users))


def can_see_area(db: Session, user: User, area: AdminArea) -> bool:
    paths = allowed_area_paths(db, user)
    return paths is None or any(area.path.startswith(p) for p in paths)
