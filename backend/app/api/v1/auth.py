from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.deps import DB, CurrentUser
from app.core.security import (
    create_access_token,
    hash_token,
    new_refresh_token,
    verify_password,
)
from app.db.models import RefreshToken, User, UserStatus
from app.schemas import LoginIn, RefreshIn, TokenOut, UserOut
from app.services.audit import audit
from app.services.rate_limit import get_limiter

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _issue_tokens(db: DB, user: User, device_id=None) -> tuple[TokenOut, RefreshToken]:  # type: ignore[no-untyped-def]
    settings = get_settings()
    access, expires_in = create_access_token(user.id, user.role.value)
    raw_refresh = new_refresh_token()
    record = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw_refresh),
        device_id=device_id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
    )
    db.add(record)
    db.flush()
    out = TokenOut(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )
    return out, record


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: DB) -> TokenOut:
    ip = _client_ip(request)
    limiter = get_limiter()
    if limiter.is_blocked(body.username, ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again later."
        )

    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        limiter.record_failure(body.username, ip)
        audit(db, "login_failed", details={"username": body.username}, ip=ip)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    if user.status != UserStatus.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    if body.device_id is not None:
        from app.db.models import Device

        device = db.get(Device, body.device_id)
        if device is not None and device.is_blocked:
            audit(
                db,
                "login_blocked_device",
                actor_id=user.id,
                entity="device",
                entity_id=device.id,
                ip=ip,
            )
            db.commit()
            raise HTTPException(
                status.HTTP_423_LOCKED, "This phone has been blocked. Contact your supervisor."
            )

    limiter.reset(body.username, ip)
    user.last_login_at = datetime.now(UTC)
    out, _ = _issue_tokens(db, user, body.device_id)
    audit(db, "login", actor_id=user.id, ip=ip)
    db.commit()
    return out


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, request: Request, db: DB) -> TokenOut:
    now = datetime.now(UTC)
    record = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(body.refresh_token))
    )
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if record is None:
        raise invalid
    if record.revoked_at is not None:
        # A revoked token being reused suggests theft: revoke every session for this user.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == record.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        audit(db, "refresh_token_reuse", actor_id=record.user_id, ip=_client_ip(request))
        db.commit()
        raise invalid
    if record.expires_at <= now:
        raise invalid
    user = db.get(User, record.user_id)
    if user is None or user.status != UserStatus.active:
        raise invalid

    out, new_record = _issue_tokens(db, user, record.device_id)
    record.revoked_at = now
    record.replaced_by_id = new_record.id
    db.commit()
    return out


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshIn, db: DB) -> None:
    record = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(body.refresh_token))
    )
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        audit(db, "logout", actor_id=record.user_id)
        db.commit()


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
