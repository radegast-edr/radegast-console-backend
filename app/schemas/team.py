from pydantic import BaseModel


class TeamCreate(BaseModel):
    name: str
    permission_pack: str | None = None
    permission_invite: str | None = None
    permission_admin: str | None = None
    permission_logs: str | None = None
    managing_team_id: int | None = None


class TeamUpdate(BaseModel):
    name: str | None = None
    permission_pack: str | None = None
    permission_invite: str | None = None
    permission_admin: str | None = None
    permission_logs: str | None = None
    managing_team_id: int | None = None


class TeamResponse(BaseModel):
    id: int
    name: str
    permission_pack: str | None
    permission_invite: str | None
    permission_admin: str | None
    permission_logs: str | None
    managing_team_id: int | None = None

    model_config = {"from_attributes": True}


class TeamInvite(BaseModel):
    email: str
    group_keys: dict[int, str] | None = None


class DeviceGroupCreate(BaseModel):
    name: str


class DeviceGroupResponse(BaseModel):
    id: int
    name: str
    private_key: str | None = None
    public_key: str | None = None

    model_config = {"from_attributes": True}


class DeviceGroupDetailResponse(BaseModel):
    id: int
    name: str
    teams: list[TeamResponse]
    private_key: str | None = None
    public_key: str | None = None
    # devices imported lazily to avoid circular import — built manually in router

    model_config = {"from_attributes": True}


class TeamMemberResponse(BaseModel):
    id: int
    email: str
    role: str

    model_config = {"from_attributes": True}


class CancelInvitationRequest(BaseModel):
    group_keys: dict[int, str]


class DeviceAddPayload(BaseModel):
    encrypted_private_key: str


class DeviceRemovePayload(BaseModel):
    encrypted_private_key: str


class GroupLinkPayload(BaseModel):
    encrypted_private_key: str


class GroupUnlinkPayload(BaseModel):
    encrypted_private_key: str


class MemberRemovePayload(BaseModel):
    group_keys: dict[int, str]


class GroupKeysSetup(BaseModel):
    public_key: str
    private_key: str
