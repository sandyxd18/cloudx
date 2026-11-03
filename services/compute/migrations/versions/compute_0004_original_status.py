"""status a server had before a resize

Revision ID: compute_0004
Revises: compute_0003
Create Date: 2026-11-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "compute_0004"
down_revision = "compute_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "compute_servers" in insp.get_table_names() and "original_status" not in {c["name"] for c in insp.get_columns("compute_servers")}:
        op.add_column("compute_servers", sa.Column("original_status", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("compute_servers", "original_status")
