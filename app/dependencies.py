import hashlib
import time
from logging import getLogger

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.middleware.session import SessionData, parse_session_cookie
from app.models.api_key import APIKey
from app.models.device import Device
from app.models.user import User
from app.schemas.user import MfaVerifyRequest, UserLogin
from app.services.auth import verify_signed_token
from app.utils import ensure_utc, utc_now


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
    except Exception as e:
        getLogger(__name__).debug(f"Error checking hardware tokens: {e}")
    if req_val == 1:  # otp
        return has_otp or has_token
    if req_val == 2:  # hardware_token/token
        return has_token
    return True


def check_api_key_permission(session: SessionData, path: str, method: str):
    if not session.api_key_id:
        return

    normalized_path = path.lower()
    for prefix in ("/api/v1", "/api/v2"):
        if normalized_path.startswith(prefix):
            normalized_path = normalized_path[len(prefix) :]

    scope = None
    if normalized_path.startswith("/devices"):
        scope = "devices"
    elif normalized_path.startswith("/teams"):
        scope = "teams"
    elif normalized_path.startswith("/groups"):
        scope = "groups"
    elif normalized_path.startswith("/packs"):
        scope = "packs"
    elif normalized_path.startswith("/logs"):
        scope = "logs"

    if scope is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This scope is not yet supported by the API",
        )

    # Check scopes dict
    allowed_val = session.api_key_scopes.get(scope, [])
    if isinstance(allowed_val, str):
        if allowed_val == "none":
            allowed = []
        elif allowed_val == "read":
            allowed = ["read"]
        elif allowed_val == "write":
            allowed = ["read", "create", "write", "delete"]
        else:
            allowed = []
    else:
        allowed = list(allowed_val)

    # Determine required permission based on method
    if method in ("GET", "OPTIONS", "HEAD"):
        required_permission = "read"
    elif method == "POST":
        required_permission = "create"
    elif method in ("PUT", "PATCH"):
        required_permission = "write"
    elif method == "DELETE":
        required_permission = "delete"
    else:
        required_permission = "read"

    if required_permission not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key does not have '{required_permission}' permission for scope '{scope}'",
        )


async def get_session_api_key(request: Request, db: AsyncSession) -> SessionData | None:
    # Check if there is an API key header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        api_key = auth_header.split(" ", 1)[1]
    else:
        api_key = request.headers.get("X-API-Key")

    if not api_key:
        return None

    # Check if it is a signed session token
    session_data = parse_session_cookie(api_key)
    if session_data:
        return session_data

    h = hashlib.sha256(api_key.encode()).hexdigest()
    result = await db.execute(select(APIKey).options(selectinload(APIKey.user)).where(APIKey.key_hash == h))
    key_record = result.scalar_one_or_none()
    if key_record:
        key_record.last_used = utc_now()
        await db.commit()

    if not key_record:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    if not key_record.user.api_keys_enabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API keys are disabled for this user",
        )

    if key_record.expires_at and key_record.expires_at < utc_now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key has expired")

    return SessionData(
        scope="user",
        id=key_record.user_id,
        issued_at=time.time(),
        mfa_level="hardware_token",  # bypass normal programmatic MFA checks
        api_key_id=key_record.id,
        api_key_scopes=key_record.scopes,
    )


def get_session_cookie(request: Request) -> SessionData | None:
    cookie = request.cookies.get(settings.session_cookie_name)
    if not cookie:
        return None
    session = parse_session_cookie(cookie)
    return session


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/token",
    auto_error=False,
    scheme_name="OAuth2Password",
)
api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    scheme_name="APIKeyHeader",
)
api_key_auth = APIKeyHeader(
    name="Authorization",
    auto_error=False,
    scheme_name="APIKeyAuthorization",
)


async def get_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _oauth2: str | None = Depends(oauth2_scheme),
    _api_key: str | None = Depends(api_key_header),
    _api_key_auth: str | None = Depends(api_key_auth),
) -> SessionData:
    session = await get_session_api_key(request, db)
    if session:
        return session

    session = get_session_cookie(request)
    if session:
        return session

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def get_current_user(
    request: Request,
    session: SessionData = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> User:
    if session.scope != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User session required")
    result = await db.execute(select(User).options(selectinload(User.hardware_tokens)).where(User.id == session.id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Check session invalidation only for non-API keys
    if session.api_key_id is None:
        if ensure_utc(user.password_change).timestamp() > session.issued_at.timestamp():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalidated")

    # Enforce role-based MFA level (only for non-API keys)
    if session.api_key_id is None:
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
        is_mfa_path = "/auth/mfa" in path or "/auth/logout" in path or "/auth/me" in path

        if not is_mfa_path:
            # Only enforce if they actually have the required MFA configured
            if user_has_required_mfa_setup(user, required_level):
                if mfa_level_value(session_mfa_level) < mfa_level_value(required_level):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"MFA level '{required_level}' required for role '{user.role.value}'",
                    )

    # Perform API key scope authorization checks
    if session.api_key_id is not None:
        check_api_key_permission(session, request.url.path, request.method)

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


def require_role(role: str):
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
            detail=f"Too many attempts. Please try again in {wait_time} seconds.",
        )
    rate_limits[key].append(now)


async def rate_limit_login(request: Request, data: UserLogin):
    ip = _client_ip(request)
    check_rate_limit(request, f"login:ip:{ip}", limit=5, window=30)
    check_rate_limit(request, f"login:email:{data.email}", limit=5, window=30)


async def rate_limit_token(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    ip = _client_ip(request)
    check_rate_limit(request, f"login:ip:{ip}", limit=5, window=30)
    check_rate_limit(request, f"login:email:{form_data.username}", limit=5, window=30)


async def rate_limit_mfa(request: Request, data: MfaVerifyRequest):
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
