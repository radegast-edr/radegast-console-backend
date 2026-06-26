from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Exclusion(Base):
    __tablename__ = "exclusions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_group_id: Mapped[int] = mapped_column(ForeignKey("device_groups.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    jsonata_query: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    alert_id: Mapped[int | None] = mapped_column(ForeignKey("logs.id", ondelete="SET NULL"), nullable=True)
    exclusion_type: Mapped[str] = mapped_column(String(20), default="hard", server_default="hard", nullable=False)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)

    device_group = relationship("DeviceGroup", back_populates="exclusions")
