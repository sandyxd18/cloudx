"""add parent_id column to orchestration_stacks

Revision ID: orchestration_0003
Revises: orchestration_0002
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "orchestration_0003"
down_revision = "orchestration_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = insp.get_table_names()
    if "orchestration_stacks" in tables:
        cols = {c["name"] for c in insp.get_columns("orchestration_stacks")}
        if "parent_id" not in cols:
            op.add_column("orchestration_stacks", sa.Column("parent_id", sa.String(64), nullable=True))
            op.create_index("ix_orchestration_stacks_parent_id", "orchestration_stacks", ["parent_id"])


def downgrade() -> None:
    pass
