from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ExclusionCreate(BaseModel):
    name: str
    jsonata_query: str
    description: str | None = None
    alert_id: int | None = None
    exclusion_type: Literal["hard", "soft"] = "hard"
    encrypted: bool = False


class ExclusionResponse(BaseModel):
    id: int
    device_group_id: int
    name: str
    description: str | None
    jsonata_query: str
    created_at: datetime
    alert_id: int | None = None
    exclusion_type: Literal["hard", "soft"] = "hard"
    encrypted: bool = False

    model_config = {"from_attributes": True}


class ExclusionDetailResponse(BaseModel):
    id: int
    device_group_id: int
    name: str
    description: str | None
    jsonata_query: str
    created_at: datetime
    alert_id: int | None = None
    exclusion_type: Literal["hard", "soft"] = "hard"
    encrypted: bool = False

    model_config = {"from_attributes": True}
