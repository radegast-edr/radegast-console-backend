from datetime import datetime

from pydantic import BaseModel


class UserRegister(BaseModel):
    email: str
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    role: str
    verified: bool
    has_keys: bool = False

    model_config = {"from_attributes": True}


class UserDetailResponse(BaseModel):
    id: int
    email: str
    role: str
    verified: bool
    password_change: datetime

    model_config = {"from_attributes": True}


class KeySetupRequest(BaseModel):
    public_key: str
    encrypted_private_key: str  # main private key AGE-encrypted with recovery public key


class KeySetupResponse(BaseModel):
    message: str


class KeyRecoverResponse(BaseModel):
    public_key: str
    encrypted_private_key: str  # client decrypts with their AGE recovery private key


# Key transfer schemas
class KeyTransferInitiateRequest(BaseModel):
    receiver_age_public_key: str  # receiver's ephemeral AGE public key


class KeyTransferInitiateResponse(BaseModel):
    transfer_id: str


class KeyTransferStatusResponse(BaseModel):
    transfer_id: str
    status: str
    receiver_age_public_key: str
    encrypted_private_key: str | None = None


class KeyTransferCompleteRequest(BaseModel):
    encrypted_private_key: str  # main private key AGE-encrypted for receiver's ephemeral key


class KeySecondarySetupRequest(BaseModel):
    """Add a secondary (recovery) keypair alongside the existing main keypair."""
    public_key: str
    encrypted_private_key: str  # private key AGE-encrypted with the matching recovery key


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class NotificationSettings(BaseModel):
    notify_login: bool
    notify_new_keys: bool
    notify_recovery_used: bool
    notify_keys_transferred: bool
    notify_device_log: bool

    model_config = {"from_attributes": True}
