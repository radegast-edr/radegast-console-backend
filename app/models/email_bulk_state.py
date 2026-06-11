from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailBulkState(Base):
    __tablename__ = "email_bulk_states"

    email_to: Mapped[str] = mapped_column(String(255), primary_key=True)
    email_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
