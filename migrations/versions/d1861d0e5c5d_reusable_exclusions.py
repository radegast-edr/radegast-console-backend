"""reusable_exclusions

Exclusions can now be applied to several device groups:

* new association table ``exclusion_device_groups`` (filled from ``exclusions.device_group_id``)
* ``exclusions.device_group_id`` is dropped
* new columns on ``exclusions``: ``version`` (optimistic locking) and the last change
  (``updated_at``, ``updated_by_user_id``, ``last_action``, ``last_action_group_id``)

Existing content (plaintext or AGE ciphertext for the single group key) is kept unchanged.

The group mapping is read before the table is rebuilt and inserted afterwards, so the
migration is safe even if SQLite foreign keys were enabled during the batch table rebuild.

Downgrade restores ``device_group_id`` from the lowest linked group id. Links to any further
groups are lost, and exclusions without any group are deleted.

Revision ID: d1861d0e5c5d
Revises: b5b028633eda
Create Date: 2026-11-09 16:56:24.463621

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1861d0e5c5d'
down_revision: Union[str, Sequence[str], None] = 'b5b028633eda'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LINK_TABLE = "exclusion_device_groups"
LINK_INDEX = "ix_exclusion_device_groups_device_group_id"
UPDATED_BY_FK = "fk_exclusions_updated_by_user_id_users"
DEVICE_GROUP_FK = "fk_exclusions_device_group_id_device_groups"


def _columns(inspector, table: str) -> list[str]:
    return [col["name"] for col in inspector.get_columns(table)]


def _fk_names(inspector, table: str, column: str) -> list[str]:
    return [
        fk["name"]
        for fk in inspector.get_foreign_keys(table)
        if fk.get("name") and column in fk.get("constrained_columns", [])
    ]


def _link_table() -> sa.Table:
    return sa.table(
        LINK_TABLE,
        sa.column("exclusion_id", sa.Integer()),
        sa.column("device_group_id", sa.Integer()),
    )


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "exclusions" not in tables:
        return

    columns = _columns(inspector, "exclusions")

    # 1. Read the existing exclusion -> group mapping before the table is rebuilt
    mapping: list[dict] = []
    if "device_group_id" in columns:
        rows = bind.execute(
            sa.text(
                "SELECT e.id, e.device_group_id FROM exclusions e "
                "JOIN device_groups g ON g.id = e.device_group_id "
                "WHERE e.device_group_id IS NOT NULL"
            )
        ).fetchall()
        mapping = [{"exclusion_id": r[0], "device_group_id": r[1]} for r in rows]

    # 2. New columns + drop device_group_id
    added_updated_at = "updated_at" not in columns
    with op.batch_alter_table("exclusions", schema=None) as batch_op:
        if "version" not in columns:
            batch_op.add_column(sa.Column("version", sa.Integer(), server_default="1", nullable=False))
        if added_updated_at:
            batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        if "updated_by_user_id" not in columns:
            batch_op.add_column(sa.Column("updated_by_user_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(UPDATED_BY_FK, "users", ["updated_by_user_id"], ["id"], ondelete="SET NULL")
        if "last_action" not in columns:
            batch_op.add_column(sa.Column("last_action", sa.String(length=20), server_default="created", nullable=False))
        if "last_action_group_id" not in columns:
            batch_op.add_column(sa.Column("last_action_group_id", sa.Integer(), nullable=True))
        if "device_group_id" in columns:
            # MySQL needs the (auto-named) foreign key dropped before the column
            for fk_name in _fk_names(inspector, "exclusions", "device_group_id"):
                batch_op.drop_constraint(fk_name, type_="foreignkey")
            batch_op.drop_column("device_group_id")

    # 3. Existing rows: mark as migrated, with no known author
    if added_updated_at:
        op.execute(sa.text("UPDATE exclusions SET last_action = 'migrated', updated_at = created_at"))

    # 4. Association table + links
    if LINK_TABLE not in inspector.get_table_names():
        op.create_table(
            LINK_TABLE,
            sa.Column("exclusion_id", sa.Integer(), nullable=False),
            sa.Column("device_group_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["exclusion_id"], ["exclusions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["device_group_id"], ["device_groups.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("exclusion_id", "device_group_id"),
        )
    if LINK_INDEX not in [idx["name"] for idx in sa.inspect(bind).get_indexes(LINK_TABLE)]:
        op.create_index(LINK_INDEX, LINK_TABLE, ["device_group_id"], unique=False)

    if mapping:
        existing = {
            (r[0], r[1]) for r in bind.execute(sa.text(f"SELECT exclusion_id, device_group_id FROM {LINK_TABLE}")).fetchall()
        }
        new_rows = [m for m in mapping if (m["exclusion_id"], m["device_group_id"]) not in existing]
        if new_rows:
            op.bulk_insert(_link_table(), new_rows)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "exclusions" not in tables:
        return

    columns = _columns(inspector, "exclusions")

    # 1. Read the lowest linked group per exclusion
    mapping: dict[int, int] = {}
    if LINK_TABLE in tables:
        rows = bind.execute(
            sa.text(f"SELECT exclusion_id, MIN(device_group_id) FROM {LINK_TABLE} GROUP BY exclusion_id")
        ).fetchall()
        mapping = {r[0]: r[1] for r in rows}

    if "device_group_id" not in columns:
        # Exclusions without any group cannot be represented in the old schema
        if mapping:
            ids = ",".join(str(i) for i in mapping)
            op.execute(sa.text(f"DELETE FROM exclusions WHERE id NOT IN ({ids})"))
        else:
            op.execute(sa.text("DELETE FROM exclusions"))
        # SQLite migrations run without foreign key enforcement, so clear references explicitly
        op.execute(sa.text("UPDATE logs SET excluded_by = NULL WHERE excluded_by IS NOT NULL AND excluded_by NOT IN (SELECT id FROM exclusions)"))

        with op.batch_alter_table("exclusions", schema=None) as batch_op:
            batch_op.add_column(sa.Column("device_group_id", sa.Integer(), nullable=True))

        for exclusion_id, group_id in mapping.items():
            op.execute(
                sa.text("UPDATE exclusions SET device_group_id = :gid WHERE id = :eid").bindparams(gid=group_id, eid=exclusion_id)
            )

        with op.batch_alter_table("exclusions", schema=None) as batch_op:
            batch_op.alter_column("device_group_id", existing_type=sa.Integer(), nullable=False)
            batch_op.create_foreign_key(DEVICE_GROUP_FK, "device_groups", ["device_group_id"], ["id"], ondelete="CASCADE")

    # 2. Drop the association table (its index goes with it; MySQL refuses to drop the
    #    index on its own because the foreign key on device_group_id uses it)
    if LINK_TABLE in sa.inspect(bind).get_table_names():
        op.drop_table(LINK_TABLE)

    # 3. Drop the new columns
    inspector = sa.inspect(bind)
    columns = _columns(inspector, "exclusions")
    with op.batch_alter_table("exclusions", schema=None) as batch_op:
        if "updated_by_user_id" in columns:
            for fk_name in _fk_names(inspector, "exclusions", "updated_by_user_id"):
                batch_op.drop_constraint(fk_name, type_="foreignkey")
            batch_op.drop_column("updated_by_user_id")
        for column in ("version", "updated_at", "last_action", "last_action_group_id"):
            if column in columns:
                batch_op.drop_column(column)
