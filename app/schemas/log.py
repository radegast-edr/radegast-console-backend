from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, field_validator

from app.models.log import LogSeverity


class TriggeredRuleResponse(BaseModel):
    rule_id: str
    rule_type: str
    pack_version_id: int
    pack_id: int | None = None
    pack_name: str | None = None
    rule_content: str


class LogCreate(BaseModel):
    time: datetime
    content: str
    signature: str | None = None
    severity: LogSeverity | None = None
    rule_id: str | None = None
    rule_type: str | None = None
    excluded_by: int | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, v: Any) -> LogSeverity | None:
        try:
            return LogSeverity(v)
        except (ValueError, TypeError):
            return None

    @field_validator("rule_type", mode="before")
    @classmethod
    def validate_rule_type(cls, v: Any) -> str | None:
        if v is None:
            return None
        if v in ("sigma", "ioc", "yara"):
            return v
        return None


class ExclusionGroupResponse(BaseModel):
    id: int
    name: str


class ExclusionRefResponse(BaseModel):
    id: int
    group: ExclusionGroupResponse


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
    rule_id: str | None = None
    rule_type: str | None = None
    triggered_rule: TriggeredRuleResponse | None = None
    excluded_by: ExclusionRefResponse | None = None

    @field_validator("time", mode="after")
    @classmethod
    def ensure_time_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

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
