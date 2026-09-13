from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PreventionAllowlist(Base):
    __tablename__ = "prevention_allowlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_group_id: Mapped[int] = mapped_column(ForeignKey("device_groups.id", ondelete="CASCADE"), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "path" or "image"
    value: Mapped[str] = mapped_column(Text, nullable=False)  # AGE-encrypted
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # AGE-encrypted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    device_group = relationship("DeviceGroup", back_populates="prevention_allowlists")
