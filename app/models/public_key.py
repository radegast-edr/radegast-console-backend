from datetime import datetime, timezone as tz
from sqlalchemy import ForeignKey, Integer, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PublicKey(Base):
    __tablename__ = "public_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_type: Mapped[str] = mapped_column(String(20), nullable=False, default="regular")
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.now(tz=tz.utc), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    user = relationship("User", back_populates="public_keys")
