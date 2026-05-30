from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PackVersion(Base):
    __tablename__ = "pack_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pack_id: Mapped[int] = mapped_column(ForeignKey("packs.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    zip_path: Mapped[str] = mapped_column(String(512), nullable=False)
    released: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    release_notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    pack = relationship("Pack", back_populates="versions")
