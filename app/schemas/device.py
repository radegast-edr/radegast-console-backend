from datetime import datetime

from pydantic import BaseModel

from app.schemas.team import DeviceGroupResponse


class DeviceCreate(BaseModel):
    name: str
    group_id: int


class DeviceResponse(BaseModel):
    id: int
    name: str
    signature_public_key: str | None
    last_seen: datetime | None = None
    agent_version: str | None = None
    rustinel_version: str | None = None

    model_config = {"from_attributes": True}


class DeviceDetailResponse(BaseModel):
    id: int
    name: str
    signature_public_key: str | None
    last_seen: datetime | None = None
    agent_version: str | None = None
    rustinel_version: str | None = None
    groups: list[DeviceGroupResponse]

    model_config = {"from_attributes": True}


class DeviceCreateResponse(BaseModel):
    id: int
    name: str
    token: str


class DeviceLogin(BaseModel):
    token: str


class DeviceRename(BaseModel):
    name: str


class DeviceSetSigningKey(BaseModel):
    signature_public_key: str
