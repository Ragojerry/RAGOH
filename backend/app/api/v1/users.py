import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from app.core.deps import DB, AdminUser, ManagerOrAdmin, StaffUser
from app.core.security import hash_password
from app.db.models import AdminArea, RefreshToken, Role, User, UserAreaAccess
from app.schemas import PasswordReset, UserCreate, UserOut, UserUpdate
from app.services.audit import audit
from app.services.scope import can_see_area, visible_user_ids_condition

router = APIRouter(prefix="/users", tags=["users"])

# Managers may create and edit field staff; only admins manage managers and admins.
MANAGER_CAN_MANAGE = {Role.collector, Role.supervisor}


def _check_manageable(actor: User, role: Role) -> None:
    if actor.role == Role.manager and role not in MANAGER_CAN_MANAGE:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Managers can only manage collectors and supervisors"
        )


def _set_areas(db: DB, actor: User, user: User, area_ids: list[uuid.UUID]) -> None:
    areas = (
        list(db.scalars(select(AdminArea).where(AdminArea.id.in_(area_ids)))) if area_ids else []
    )
    if len(areas) != len(set(area_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more areas not found")
    for a in areas:
        if not can_see_area(db, actor, a):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Area {a.code} is outside your scope")
    db.execute(delete(UserAreaAccess).where(UserAreaAccess.user_id == user.id))
    for a in areas:
        db.add(UserAreaAccess(user_id=user.id, admin_area_id=a.id))


def _get_visible(db: DB, actor: User, user_id: uuid.UUID) -> User:
    user = db.scalar(select(User).where(User.id == user_id, visible_user_ids_condition(db, actor)))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("", response_model=list[UserOut])
def list_users(db: DB, actor: StaffUser, role: Role | None = None) -> list[User]:
    stmt = select(User).where(visible_user_ids_condition(db, actor)).order_by(User.full_name)
    if role is not None:
        stmt = stmt.where(User.role == role)
    return list(db.scalars(stmt))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: DB, actor: ManagerOrAdmin) -> User:
    _check_manageable(actor, body.role)
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
    user = User(
        organisation_id=actor.organisation_id,
        username=body.username,
        full_name=body.full_name,
        phone=body.phone,
        email=body.email,
        role=body.role,
        language=body.language,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.flush()
    _set_areas(db, actor, user, body.area_ids)
    audit(
        db,
        "user_create",
        actor_id=actor.id,
        entity="user",
        entity_id=user.id,
        details={"role": body.role.value},
    )
    db.commit()
    return user


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, db: DB, actor: StaffUser) -> User:
    return _get_visible(db, actor, user_id)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, db: DB, actor: ManagerOrAdmin) -> User:
    user = _get_visible(db, actor, user_id)
    _check_manageable(actor, user.role)
    data = body.model_dump(exclude_unset=True)
    area_ids = data.pop("area_ids", None)
    for key, value in data.items():
        setattr(user, key, value)
    if area_ids is not None:
        _set_areas(db, actor, user, area_ids)
    if body.status is not None and body.status.value == "disabled":
        db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    audit(
        db,
        "user_update",
        actor_id=actor.id,
        entity="user",
        entity_id=user.id,
        details={"fields": list(data)},
    )
    db.commit()
    return user


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(user_id: uuid.UUID, body: PasswordReset, db: DB, actor: AdminUser) -> None:
    user = _get_visible(db, actor, user_id)
    user.password_hash = hash_password(body.new_password)
    db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    audit(db, "password_reset", actor_id=actor.id, entity="user", entity_id=user.id)
    db.commit()
