from pydantic import BaseModel


class DeviceCreate(BaseModel):
    name: str
    group_id: int


class DeviceResponse(BaseModel):
    id: int
    name: str
    signature_public_key: str | None

    model_config = {"from_attributes": True}


class DeviceCreateResponse(BaseModel):
    id: int
    name: str
    token: str


class DeviceLogin(BaseModel):
    token: str


class DeviceSetSigningKey(BaseModel):
    signature_public_key: str
