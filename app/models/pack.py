from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.attributes import instance_state

from app.database import Base
from app.models.associations import pack_teams


class Pack(Base):
    __tablename__ = "packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pack_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    versions = relationship("PackVersion", back_populates="pack", cascade="all, delete-orphan")
    creator = relationship("User")
    teams = relationship("Team", secondary=pack_teams, back_populates="packs")

    @property
    def team_ids(self) -> list[int]:
        state = instance_state(self)
        if "teams" in state.dict:
            return [t.id for t in self.teams]
        return []
