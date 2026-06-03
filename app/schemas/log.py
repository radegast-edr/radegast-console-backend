from datetime import datetime
from pydantic import field_validator, BaseModel
from typing import Any

from app.schemas.common import SigmaLevel

class LogCreate(BaseModel):
    time: datetime
    content: str
    signature: str | None = None
    severity: SigmaLevel | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, v: Any) -> SigmaLevel | None:
        if not v:
            return None
        v_lower = str(v).lower()
        if v_lower in ["informational", "low", "medium", "high", "critical"]:
            return v_lower # type: ignore
        return None


class LogResponse(BaseModel):
    id: int
    device_id: int
    time: datetime
    content: str
    signature: str | None
    seen: bool = False
    severity: SigmaLevel | None = None

    model_config = {"from_attributes": True}
