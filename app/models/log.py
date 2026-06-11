import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, Text, String, Enum, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LogSeverity(str, enum.Enum):
    informational = "informational"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[LogSeverity | None] = mapped_column(Enum(LogSeverity), nullable=True, index=True)
    triage_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    alert_resolution: Mapped[str | None] = mapped_column(String(50), default=None, server_default=None, nullable=True, index=True)
    device = relationship("Device", back_populates="logs")

    __table_args__ = (
        Index("idx_logs_device_id_time", "device_id", "time"),
        Index("idx_logs_time_severity", "time", "severity"),
    )


class LogSeen(Base):
    __tablename__ = "logs_seen"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    log_id: Mapped[int] = mapped_column(
        ForeignKey("logs.id", ondelete="CASCADE"), primary_key=True
    )
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User")
    log = relationship("Log")
