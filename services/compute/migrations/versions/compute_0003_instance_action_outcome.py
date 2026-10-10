"""instance action outcome fields

Revision ID: compute_0003
Revises: compute_0002
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "compute_0003"
down_revision = "compute_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = insp.get_table_names()
    if "compute_server_actions" in tables:
        cols = {c["name"] for c in insp.get_columns("compute_server_actions")}
        if "result" not in cols:
            op.add_column("compute_server_actions", sa.Column("result", sa.String(), nullable=True))
        if "message" not in cols:
            op.add_column("compute_server_actions", sa.Column("message", sa.Text(), nullable=True))
        if "finish_time" not in cols:
            op.add_column("compute_server_actions", sa.Column("finish_time", sa.DateTime(), nullable=True))


def downgrade() -> None:
    pass
