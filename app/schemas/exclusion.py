from datetime import datetime

from pydantic import BaseModel


class ExclusionCreate(BaseModel):
    name: str
    jsonata_query: str
    description: str | None = None


class ExclusionResponse(BaseModel):
    id: int
    device_group_id: int
    name: str
    description: str | None
    jsonata_query: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ExclusionDetailResponse(BaseModel):
    id: int
    device_group_id: int
    name: str
    description: str | None
    jsonata_query: str
    created_at: datetime

    model_config = {"from_attributes": True}
