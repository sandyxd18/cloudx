"""initial schema for reservation

Revision ID: reservation_0001
Revises:
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "reservation_0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    if "rsv_leases" not in set(inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "rsv_leases",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("start_at", sa.DateTime(), nullable=False),
            sa.Column("end_at", sa.DateTime(), nullable=False),
            sa.Column("flavor", sa.String(), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("vcpus", sa.Integer(), nullable=False),
            sa.Column("ram_mb", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("project_id", "name"),
        )
        op.create_index("ix_rsv_leases_project_id", "rsv_leases", ["project_id"])

def downgrade() -> None:
    op.drop_table("rsv_leases")
