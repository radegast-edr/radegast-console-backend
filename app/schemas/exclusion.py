from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ExclusionType = Literal["hard", "soft"]
ExclusionLastAction = Literal["created", "updated", "applied", "removed", "migrated"]


class ExclusionCreate(BaseModel):
    """Create an exclusion in a single device group (kept for backwards compatibility)."""

    name: str
    jsonata_query: str
    description: str | None = None
    alert_id: int | None = None
    exclusion_type: ExclusionType = "hard"
    encrypted: bool = False


class ExclusionCreateMulti(BaseModel):
    """Create an exclusion applied to one or more device groups.

    When ``encrypted`` is true, the content fields must be AGE ciphertext encrypted for the
    public key of every group in ``group_ids``.
    """

    name: str
    jsonata_query: str
    description: str | None = None
    group_ids: list[int] = Field(min_length=1)
    alert_id: int | None = None
    exclusion_type: ExclusionType = "hard"
    encrypted: bool = False


class ExclusionUpdate(BaseModel):
    """Update the content of an exclusion in all groups it is applied to."""

    version: int
    name: str
    jsonata_query: str
    description: str | None = None
    exclusion_type: ExclusionType | None = None
    encrypted: bool | None = None


class ExclusionGroupChange(BaseModel):
    """Apply an exclusion to a group or remove it from one.

    For encrypted exclusions the content must be re-encrypted for the new set of groups
    (not needed when removing the last group, which deletes the exclusion).
    """

    version: int
    name: str | None = None
    jsonata_query: str | None = None
    description: str | None = None


class ExclusionGroupRef(BaseModel):
    id: int
    name: str


class ExclusionLastChange(BaseModel):
    action: ExclusionLastAction
    at: datetime | None = None
    by_user_id: int | None = None
    by_email: str | None = None
    group_id: int | None = None
    group_name: str | None = None


class ExclusionResponse(BaseModel):
    id: int
    # Kept for backwards compatibility: the group in the current context, otherwise the first linked group.
    device_group_id: int | None = None
    name: str
    description: str | None
    jsonata_query: str
    created_at: datetime
    alert_id: int | None = None
    exclusion_type: ExclusionType = "hard"
    encrypted: bool = False
    version: int = 1
    # Linked groups visible to the current user, and the total number of linked groups.
    groups: list[ExclusionGroupRef] = []
    group_count: int = 0
    can_edit: bool = False
    last_change: ExclusionLastChange | None = None


class ExclusionRecipientsResponse(BaseModel):
    version: int
    public_keys: list[str]


class ExclusionDeleteResponse(BaseModel):
    message: str
    deleted: bool = False
