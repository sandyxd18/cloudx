"""when a server entered VERIFY_RESIZE

Revision ID: compute_0005
Revises: compute_0004
Create Date: 2026-11-03
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "compute_0005"
down_revision = "compute_0004"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "compute_servers" not in inspect(op.get_bind()).get_table_names():
        return
    if "resize_pending_since" not in _cols("compute_servers"):
        op.add_column("compute_servers", sa.Column("resize_pending_since", sa.DateTime(), nullable=True))
    # servers already waiting for confirm/revert get a full window from now
    op.get_bind().execute(
        sa.text(
            "UPDATE compute_servers SET resize_pending_since = :now WHERE status = 'VERIFY_RESIZE' AND resize_pending_since IS NULL"
        ).bindparams(sa.bindparam("now", type_=sa.DateTime())),
        {"now": datetime.now(timezone.utc).replace(tzinfo=None)},
    )


def downgrade() -> None:
    op.drop_column("compute_servers", "resize_pending_since")
