from pydantic import BaseModel


class TeamCreate(BaseModel):
    name: str
    permission_pack: str | None = None
    permission_invite: str | None = None
    permission_admin: str | None = None
    permission_logs: str | None = None


class TeamUpdate(BaseModel):
    name: str | None = None
    permission_pack: str | None = None
    permission_invite: str | None = None
    permission_admin: str | None = None
    permission_logs: str | None = None


class TeamResponse(BaseModel):
    id: int
    name: str
    permission_pack: str | None
    permission_invite: str | None
    permission_admin: str | None
    permission_logs: str | None

    model_config = {"from_attributes": True}


class TeamInvite(BaseModel):
    email: str


class DeviceGroupCreate(BaseModel):
    name: str


class DeviceGroupResponse(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class DeviceGroupDetailResponse(BaseModel):
    id: int
    name: str
    teams: list[TeamResponse]
    # devices imported lazily to avoid circular import — built manually in router

    model_config = {"from_attributes": True}
