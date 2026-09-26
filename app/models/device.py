from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.associations import device_group_devices


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token: Mapped[str] = mapped_column(String(255), nullable=False)
    token_change: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signature_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    encryption_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    healthy: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rustinel_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    os: Mapped[str | None] = mapped_column(String(50), nullable=True)
    total_space_used: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)

    groups = relationship("DeviceGroup", secondary=device_group_devices, back_populates="devices")
    logs = relationship("Log", back_populates="device", cascade="all, delete-orphan")
