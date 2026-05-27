from datetime import datetime

from pydantic import BaseModel, EmailStr


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

    model_config = {"from_attributes": True}


class UserDetailResponse(BaseModel):
    id: int
    email: str
    role: str
    verified: bool
    password_change: datetime

    model_config = {"from_attributes": True}


class KeySetupResponse(BaseModel):
    public_key: str
    recovery_key: str


class KeyRecoverRequest(BaseModel):
    recovery_key: str


class KeyRecoverResponse(BaseModel):
    private_key: str
    public_key: str
