"""volumes remember which storage node owns them

Revision ID: block_store_0002
Revises: block_store_0001
Create Date: 2026-10-05

LVM volumes are local to one node, so every operation on them must reach the worker of that node. NULL keeps the
old behaviour (shared queue) for NFS/Ceph volumes and for volumes created before this revision.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "block_store_0002"
down_revision = "block_store_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in inspect(op.get_bind()).get_columns("block_store_volumes")}
    if "host" not in columns:
        op.add_column("block_store_volumes", sa.Column("host", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("block_store_volumes", "host")
