from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)

    device = relationship("Device", back_populates="logs")


class LogSeen(Base):
    __tablename__ = "logs_seen"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    log_id: Mapped[int] = mapped_column(
        ForeignKey("logs.id", ondelete="CASCADE"), primary_key=True
    )
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User")
    log = relationship("Log")
