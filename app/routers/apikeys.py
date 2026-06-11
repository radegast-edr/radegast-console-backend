import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.api_key import APIKey
from app.models.user import User
from app.schemas.apikeys import APIKeyCreate, APIKeyCreatedResponse, APIKeyResponse
from app.services.email import send_api_key_created_notification

router = APIRouter(prefix="/apikeys", tags=["apikeys"])


@router.get("/", response_model=list[APIKeyResponse])
async def list_api_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not user.api_keys_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys support is disabled in settings",
        )
    result = await db.execute(select(APIKey).where(APIKey.user_id == user.id))
    keys = result.scalars().all()
    return keys


@router.post("/", response_model=APIKeyCreatedResponse)
async def create_api_key(
    data: APIKeyCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not user.api_keys_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys support is disabled in settings",
        )

    # Generate random API key: rg_ followed by 32 bytes of urlsafe base64 token
    raw_key = f"rg_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    prefix = raw_key[:12]  # e.g. rg_xxxxx...

    scopes_dict = {
        "devices": data.scopes.devices,
        "teams": data.scopes.teams,
        "groups": data.scopes.groups,
        "packs": data.scopes.packs,
        "logs": data.scopes.logs,
    }

    db_key = APIKey(
        user_id=user.id,
        name=data.name,
        key_hash=key_hash,
        prefix=prefix,
        scopes=scopes_dict,
        created_at=datetime.now(UTC),
        expires_at=data.expires_at,
    )
    db.add(db_key)
    await db.commit()
    await db.refresh(db_key)

    if user.notify_api_key_modification:
        background_tasks.add_task(send_api_key_created_notification, user.email, db_key.name, scopes_dict)

    # Return created key including the raw value (shown only once!)
    response = APIKeyCreatedResponse(
        id=db_key.id,
        name=db_key.name,
        scopes=data.scopes,
        created_at=db_key.created_at,
        expires_at=db_key.expires_at,
        last_used=db_key.last_used,
        key=raw_key,
    )
    return response


@router.delete("/{key_id}")
async def delete_api_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not user.api_keys_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys support is disabled in settings",
        )
    result = await db.execute(select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id))
    key_record = result.scalar_one_or_none()
    if not key_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    await db.delete(key_record)
    await db.commit()
    return {"message": "API key deleted successfully"}
