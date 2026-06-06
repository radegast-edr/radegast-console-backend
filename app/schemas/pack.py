from datetime import datetime

from pydantic import BaseModel


class PackCreate(BaseModel):
    name: str
    pack_id: str | None = None
    description: str = ""
    team_ids: list[int] | None = None


class PackUpdate(BaseModel):
    name: str | None = None
    pack_id: str | None = None
    description: str | None = None
    team_ids: list[int] | None = None


class PackVersionResponse(BaseModel):
    id: int
    pack_id: int
    version: str
    released: datetime
    release_notes: str | None = None
    meta: dict | None = None

    model_config = {"from_attributes": True}


class PackResponse(BaseModel):
    id: int
    pack_id: str
    name: str
    description: str
    creator_id: int | None = None
    team_ids: list[int] = []
    latest: PackVersionResponse | None = None

    model_config = {"from_attributes": True}


class PackEnabledCreate(BaseModel):
    pack_version_id: int
    autoupdate: bool = True


class PackEnabledResponse(BaseModel):
    id: int
    pack_version_id: int
    autoupdate: bool
    pack_name: str | None = None
    pack_id: str | None = None
    version: str | None = None

    model_config = {"from_attributes": True}
