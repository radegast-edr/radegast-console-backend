from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.associations import pack_teams, team_device_groups, team_users


class PermissionPack(StrEnum):
    read = "read"
    write = "write"


class PermissionInvite(StrEnum):
    write = "write"


class PermissionAdmin(StrEnum):
    write = "write"


class PermissionLogs(StrEnum):
    read = "read"


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    permission_pack: Mapped[PermissionPack | None] = mapped_column(Enum(PermissionPack), nullable=True)
    permission_invite: Mapped[PermissionInvite | None] = mapped_column(Enum(PermissionInvite), nullable=True)
    permission_admin: Mapped[PermissionAdmin | None] = mapped_column(Enum(PermissionAdmin), nullable=True)
    permission_logs: Mapped[PermissionLogs | None] = mapped_column(Enum(PermissionLogs), nullable=True)
    managing_team_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("teams.id"), nullable=True)

    users = relationship("User", secondary=team_users, back_populates="teams")
    groups = relationship("DeviceGroup", secondary=team_device_groups, back_populates="teams")
    managing_team: Mapped["Team | None"] = relationship("Team", remote_side=[id], backref="managed_teams")
    packs = relationship("Pack", secondary=pack_teams, back_populates="teams")
