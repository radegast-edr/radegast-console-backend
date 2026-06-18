from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RuleType(StrEnum):
    sigma = "sigma"
    ioc = "ioc"
    yara = "yara"


class PackVersionRule(Base):
    """Caches rule content for every unique (rule_id, rule_type, pack_version_id) combination
    that has actually triggered an alert.  This avoids re-opening zip archives on every alert."""

    __tablename__ = "pack_version_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(512), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    pack_version_id: Mapped[int] = mapped_column(ForeignKey("pack_versions.id", ondelete="CASCADE"), nullable=False)
    rule_content: Mapped[str] = mapped_column(Text, nullable=False)

    pack_version = relationship("PackVersion")

    __table_args__ = (
        UniqueConstraint("rule_id", "rule_type", "pack_version_id", name="uq_pack_version_rules_rule_pack"),
        Index("idx_pack_version_rules_rule_id", "rule_id"),
        Index("idx_pack_version_rules_rule_type", "rule_type"),
        Index("idx_pack_version_rules_pack_version_id", "pack_version_id"),
    )
