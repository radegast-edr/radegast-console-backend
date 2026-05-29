from datetime import datetime

from pydantic import BaseModel


class LogCreate(BaseModel):
    time: datetime
    content: str
    signature: str | None = None


class LogResponse(BaseModel):
    id: int
    device_id: int
    time: datetime
    content: str
    signature: str | None
    seen: bool = False

    model_config = {"from_attributes": True}
