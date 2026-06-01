import time

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.middleware.session import SessionData, parse_session_cookie
from app.models.device import Device
from app.models.user import User
from app.schemas.user import MfaVerifyRequest, UserLogin
from app.utils import ensure_utc


def mfa_level_value(level: str) -> int:
    lvl = level.lower()
    if lvl in ("hardware_token", "token"):
        return 2
    if lvl == "otp":
        return 1
    return 0


def user_has_required_mfa_setup(user: User, required_level: str) -> bool:
    req_val = mfa_level_value(required_level)
    if req_val == 0:
        return True
    has_otp = bool(user.otp_enabled and user.otp_secret)
    has_token = False
    try:
        has_token = bool(user.hardware_tokens and len(user.hardware_tokens) > 0)
    except Exception:
        pass
    if req_val == 1:  # otp
        return has_otp or has_token
    elif req_val == 2:  # hardware_token/token
        return has_token
    return True


async def get_session(request: Request) -> SessionData:
    cookie = request.cookies.get(settings.session_cookie_name)
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session = parse_session_cookie(cookie)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    return session


async def get_current_user(
    request: Request,
    session: SessionData = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> User:
    if session.scope != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User session required")
    result = await db.execute(
        select(User).options(selectinload(User.hardware_tokens)).where(User.id == session.id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if ensure_utc(user.password_change).timestamp() > session.issued_at.timestamp():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalidated")

    # Enforce role-based MFA level
    required_level = "none"
    if user.role.value == "admin":
        required_level = settings.mfa_required_level_admin
    elif user.role.value == "maintainer":
        required_level = settings.mfa_required_level_maintainer
    elif user.role.value == "user":
        required_level = settings.mfa_required_level_user

    session_mfa_level = getattr(session, "mfa_level", "none")

    # Bypass enforcement for MFA setup, verification, profile, and logout
    path = request.url.path
    is_mfa_path = (
        "/auth/mfa" in path
        or "/auth/logout" in path
        or "/auth/me" in path
    )

    if not is_mfa_path:
        # Only enforce if they actually have the required MFA configured
        if user_has_required_mfa_setup(user, required_level):
            if mfa_level_value(session_mfa_level) < mfa_level_value(required_level):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"MFA level '{required_level}' required for role '{user.role.value}'",
                )

    return user


async def get_current_device(
    session: SessionData = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> Device:
    if session.scope != "device":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device session required")
    result = await db.execute(select(Device).where(Device.id == session.id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Device not found")
    if device.token_change and ensure_utc(device.token_change).timestamp() > session.issued_at.timestamp():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalidated")
    return device


async def require_role(role: str):
    """Factory for role-based dependencies."""

    async def _check(user: User = Depends(get_current_user)) -> User:
        roles_hierarchy = ["user", "maintainer", "admin"]
        user_level = roles_hierarchy.index(user.role.value)
        required_level = roles_hierarchy.index(role)
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' or higher required",
            )
        return user

    return _check


RequireMaintainer = Depends(require_role("maintainer"))
RequireAdmin = Depends(require_role("admin"))


def _client_ip(request: Request) -> str:
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        val = request.headers.get(header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request, key: str, limit: int, window: float):
    rate_limits = request.app.state.rate_limits

    now = time.time()
    timestamps = [t for t in rate_limits[key] if now - t < window]
    rate_limits[key] = timestamps
    if len(timestamps) >= limit:
        wait_time = int(window - (now - timestamps[0]))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Please try again in {wait_time} seconds."
        )
    rate_limits[key].append(now)


async def rate_limit_login(request: Request, data: UserLogin):
    ip = _client_ip(request)
    check_rate_limit(request, f"login:ip:{ip}", limit=5, window=30)
    check_rate_limit(request, f"login:email:{data.email}", limit=5, window=30)


async def rate_limit_mfa(request: Request, data: MfaVerifyRequest):
    from app.services.auth import verify_signed_token
    ip = _client_ip(request)
    check_rate_limit(request, f"mfa:ip:{ip}", limit=5, window=30)

    token_data = verify_signed_token(data.mfa_token, salt="mfa-login", max_age=300)
    if not token_data or "user_id" not in token_data:
        raise HTTPException(status_code=400, detail="Invalid or expired MFA token")

    user_id = token_data["user_id"]
    check_rate_limit(request, f"mfa:user:{user_id}", limit=5, window=30)


async def rate_limit_mfa_otp(request: Request, user: User = Depends(get_current_user)):
    ip = _client_ip(request)
    check_rate_limit(request, f"mfa_setup:ip:{ip}", limit=5, window=30)
    check_rate_limit(request, f"mfa_setup:user:{user.id}", limit=5, window=30)
