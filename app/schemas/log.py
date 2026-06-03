from datetime import datetime
from pydantic import BaseModel

from app.schemas.common import SigmaLevel


class LogCreate(BaseModel):
    time: datetime
    content: str
    signature: str | None = None
    severity: SigmaLevel | None = None


class LogResponse(BaseModel):
    id: int
    device_id: int
    time: datetime
    content: str
    signature: str | None
    seen: bool = False
    severity: SigmaLevel | None = None

    model_config = {"from_attributes": True}
