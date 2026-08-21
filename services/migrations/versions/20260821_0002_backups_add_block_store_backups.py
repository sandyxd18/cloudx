"""add block_store_backups table (volume backups to object storage)

Revision ID: 0002_backups
Revises: base_0001
Create Date: 2026-08-21

Additive-only: creates the new table used by the Fase 5 volume-backup feature.
Safe to apply while the API is running (no changes to existing tables).
Note: block-store-api also creates this via create_all() for single-node dev;
this migration keeps multi-node deployments explicit and versioned.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_backups"
down_revision = "base_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "block_store_backups" in insp.get_table_names():
        return  # already created by create_all() on single-node dev
    op.create_table(
        "block_store_backups",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), index=True),
        sa.Column("volume_id", sa.String(), index=True),
        sa.Column("size_gb", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), server_default="creating"),
        sa.Column("backend", sa.String(), nullable=True),
        sa.Column("object_key", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True, index=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("block_store_backups")
