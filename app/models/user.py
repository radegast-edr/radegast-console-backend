from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.associations import team_users
from app.models.log import LogSeverity


class UserRole(StrEnum):
    user = "user"
    maintainer = "maintainer"
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.user, nullable=False)
    password_change: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now(UTC), nullable=False)
    registered_on: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.now(UTC), nullable=False)
    verified: Mapped[bool] = mapped_column(default=False, nullable=False)

    # MFA fields
    otp_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    otp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Email notification preferences
    notify_login: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_new_keys: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_recovery_used: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_keys_transferred: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_device_log: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_downtime_maintenance: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_api_key_modification: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    notify_news_updates: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    notification_level: Mapped[LogSeverity] = mapped_column(Enum(LogSeverity), default="medium", server_default="medium", nullable=False)
    extended_edr_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    api_keys_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    public_keys = relationship("PublicKey", back_populates="user", cascade="all, delete-orphan")
    teams = relationship("Team", secondary=team_users, back_populates="users")
    hardware_tokens = relationship("HardwareToken", back_populates="user", cascade="all, delete-orphan")
