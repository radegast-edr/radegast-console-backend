from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AccessLevel = Literal["read", "create", "write", "delete"]


class APIKeyScopes(BaseModel):
    devices: list[AccessLevel] = []
    teams: list[AccessLevel] = []
    groups: list[AccessLevel] = []
    packs: list[AccessLevel] = []
    logs: list[AccessLevel] = []


class APIKeyCreate(BaseModel):
    name: str
    scopes: APIKeyScopes
    expires_at: datetime | None = None


class APIKeyResponse(BaseModel):
    id: int
    name: str
    scopes: APIKeyScopes
    created_at: datetime
    expires_at: datetime | None = None
    last_used: datetime | None = None

    model_config = {"from_attributes": True}


class APIKeyCreatedResponse(APIKeyResponse):
    key: str
