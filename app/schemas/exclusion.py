from datetime import datetime

from pydantic import BaseModel


class ExclusionCreate(BaseModel):
    name: str
    jsonata_query: str
    description: str | None = None
    alert_id: int | None = None


class ExclusionResponse(BaseModel):
    id: int
    device_group_id: int
    name: str
    description: str | None
    jsonata_query: str
    created_at: datetime
    alert_id: int | None = None

    model_config = {"from_attributes": True}


class ExclusionDetailResponse(BaseModel):
    id: int
    device_group_id: int
    name: str
    description: str | None
    jsonata_query: str
    created_at: datetime
    alert_id: int | None = None

    model_config = {"from_attributes": True}
