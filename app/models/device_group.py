from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.associations import device_group_devices, team_device_groups


class DeviceGroup(Base):
    __tablename__ = "device_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    devices = relationship(
        "Device", secondary=device_group_devices, back_populates="groups"
    )
    teams = relationship(
        "Team", secondary=team_device_groups, back_populates="groups"
    )
    packs = relationship("PackEnabled", back_populates="device_group", cascade="all, delete-orphan")
