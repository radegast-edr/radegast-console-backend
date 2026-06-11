from sqlalchemy import Column, ForeignKey, Table

from app.database import Base

team_users = Table(
    "team_users",
    Base.metadata,
    Column("team_id", ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)

team_device_groups = Table(
    "team_device_groups",
    Base.metadata,
    Column("team_id", ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "device_group_id",
        ForeignKey("device_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

device_group_devices = Table(
    "device_group_devices",
    Base.metadata,
    Column(
        "device_group_id",
        ForeignKey("device_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("device_id", ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
)


pack_teams = Table(
    "pack_teams",
    Base.metadata,
    Column("pack_id", ForeignKey("packs.id", ondelete="CASCADE"), primary_key=True),
    Column("team_id", ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
)
