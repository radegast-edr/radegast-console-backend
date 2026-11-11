from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.associations import exclusion_device_groups


class Exclusion(Base):
    """A JSONata exclusion that can be applied to one or more device groups.

    When ``encrypted`` is true, ``name``, ``jsonata_query`` and ``description`` hold a single
    AGE ciphertext encrypted (in the browser) for the public key of every linked device group.
    """

    __tablename__ = "exclusions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    jsonata_query: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    alert_id: Mapped[int | None] = mapped_column(ForeignKey("logs.id", ondelete="SET NULL"), nullable=True)
    exclusion_type: Mapped[str] = mapped_column(String(20), default="hard", server_default="hard", nullable=False)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)

    # Optimistic locking: incremented on every change, clients must send the version they edited.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)

    # Last change (who / what / when). The actor is resolved to the current user email at read time.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_action: Mapped[str] = mapped_column(String(20), default="created", server_default="created", nullable=False)
    # Intentionally not a foreign key so the reference survives deletion of the group.
    last_action_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    device_groups = relationship("DeviceGroup", secondary=exclusion_device_groups, back_populates="exclusions")
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])

    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": version}
