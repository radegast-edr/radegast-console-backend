"""change_exclusion_name_to_text

Revision ID: 1140a2fe5b93
Revises: 782a80039b93
Create Date: 2026-09-26 09:37:10.826087

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1140a2fe5b93'
down_revision: Union[str, Sequence[str], None] = '782a80039b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "exclusions" in tables:
        with op.batch_alter_table("exclusions", schema=None) as batch_op:
            batch_op.alter_column(
                "name",
                existing_type=sa.String(length=255),
                type_=sa.Text(),
                existing_nullable=False,
            )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "exclusions" in tables:
        with op.batch_alter_table("exclusions", schema=None) as batch_op:
            batch_op.alter_column(
                "name",
                existing_type=sa.Text(),
                type_=sa.String(length=255),
                existing_nullable=False,
            )
