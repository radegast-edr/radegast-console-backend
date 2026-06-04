from datetime import datetime

from pydantic import BaseModel


class UserRegister(BaseModel):
    email: str
    password: str
    turnstile_token: str | None = None


class UserLogin(BaseModel):
    email: str
    password: str
    public_key: str | None = None


class UserResponse(BaseModel):
    id: int
    email: str
    role: str
    verified: bool
    has_keys: bool = False
    mfa_required_level: str = "none"
    mfa_setup_missing: bool = False
    mfa_configured_level: str = "none"
    extended_edr_enabled: bool = False

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
    recovery_public_key: str
    recovery_encrypted_private_key: str
    name: str | None = None


class KeySetupResponse(BaseModel):
    message: str


class KeyRecoverResponse(BaseModel):
    public_key: str
    encrypted_private_key: str


class PublicKeyResponse(BaseModel):
    id: int
    public_key: str
    key_type: str
    has_private_key: bool
    name: str | None = None
    last_used_at: datetime | None = None

    model_config = {"from_attributes": True}


class PublicKeyAddRequest(BaseModel):
    public_key: str
    encrypted_private_key: str | None = None
    key_type: str = "regular"
    name: str | None = None



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


from app.schemas.common import SigmaLevel

class NotificationSettings(BaseModel):
    notify_login: bool
    notify_new_keys: bool
    notify_recovery_used: bool
    notify_keys_transferred: bool
    notify_device_log: bool
    notify_downtime_maintenance: bool
    notification_level: SigmaLevel = "medium"

    model_config = {"from_attributes": True}


class ExtendedEdrSettings(BaseModel):
    extended_edr_enabled: bool

    model_config = {"from_attributes": True}


class MfaOtpSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str


class MfaOtpVerifyRequest(BaseModel):
    code: str


class MfaHardwareTokenSetupResponse(BaseModel):
    options: dict
    registration_token: str


class MfaHardwareTokenVerifyRequest(BaseModel):
    registration_token: str
    credential_response: dict
    name: str | None = None


class MfaHardwareTokenAssertionOptionsRequest(BaseModel):
    mfa_token: str


class MfaHardwareTokenAssertionOptionsResponse(BaseModel):
    options: dict
    assertion_token: str


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    method: str
    otp_code: str | None = None
    assertion_token: str | None = None
    webauthn_response: dict | None = None


class MfaHardwareTokenResponse(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class MfaSettingsResponse(BaseModel):
    otp_enabled: bool
    hardware_tokens: list[MfaHardwareTokenResponse]
    required_level: str
    current_level: str


class UnsubscribeRequest(BaseModel):
    token: str

