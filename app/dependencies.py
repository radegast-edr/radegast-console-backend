from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.session import SessionData, parse_session_cookie
from app.models.user import User
from app.models.device import Device
from app.config import settings


async def get_session(request: Request) -> SessionData:
    cookie = request.cookies.get(settings.session_cookie_name)
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session = parse_session_cookie(cookie)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    return session


async def get_current_user(
    session: SessionData = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> User:
    if session.scope != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User session required")
    result = await db.execute(select(User).where(User.id == session.id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.password_change > session.issued_at:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalidated")
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
    if device.token_change and device.token_change > session.issued_at:
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
