from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PreventionAllowlistCreate(BaseModel):
    entry_type: Literal["path", "image"]
    value: str
    description: str | None = None


class PreventionAllowlistResponse(BaseModel):
    id: int
    device_group_id: int
    entry_type: Literal["path", "image"]
    value: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
