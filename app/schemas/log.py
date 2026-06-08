from datetime import datetime
from pydantic import field_validator, BaseModel
from typing import Any

from app.models.log import LogSeverity


class LogCreate(BaseModel):
    time: datetime
    content: str
    signature: str | None = None
    severity: LogSeverity | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, v: Any) -> LogSeverity | None:
        try:
            return LogSeverity(v)
        except (ValueError, TypeError):
            return None


class LogResponse(BaseModel):
    id: int
    device_id: int
    time: datetime
    content: str
    signature: str | None
    seen: bool = False
    severity: LogSeverity | None = None
    alert_resolution: str | None = None
    triage_note: str | None = None

    model_config = {"from_attributes": True}


class LogResolveRequest(BaseModel):
    alert_resolution: str | None = None
    triage_note: str | None = None


class LogCountResponse(BaseModel):
    total_count: int


class DevicePublicKeyResponse(BaseModel):
    user_id: int
    public_key: str
    key_type: str
