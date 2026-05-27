from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PackEnabled(Base):
    __tablename__ = "pack_enabled"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_group_id: Mapped[int] = mapped_column(
        ForeignKey("device_groups.id", ondelete="CASCADE"), nullable=False
    )
    pack_version_id: Mapped[int] = mapped_column(
        ForeignKey("pack_versions.id", ondelete="CASCADE"), nullable=False
    )
    autoupdate: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    device_group = relationship("DeviceGroup", back_populates="packs")
    pack_version = relationship("PackVersion")
