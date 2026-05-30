from datetime import datetime

from pydantic import BaseModel


class PackCreate(BaseModel):
    name: str
    description: str = ""


class PackUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class PackResponse(BaseModel):
    id: int
    name: str
    description: str

    model_config = {"from_attributes": True}


class PackVersionResponse(BaseModel):
    id: int
    pack_id: int
    version: str
    released: datetime
    release_notes: str | None = None

    model_config = {"from_attributes": True}


class PackEnabledCreate(BaseModel):
    pack_version_id: int
    autoupdate: bool = True


class PackEnabledResponse(BaseModel):
    id: int
    pack_version_id: int
    autoupdate: bool
    pack_name: str | None = None
    version: str | None = None

    model_config = {"from_attributes": True}
