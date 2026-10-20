"""["ct_containers"].ready_at

Revision ID: container_0003
Revises: container_0002
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "container_0003"
down_revision = "container_0002"
branch_labels = None
depends_on = None

TABLES = ["ct_containers"]

def upgrade() -> None:
    insp = inspect(op.get_bind())
    for t in TABLES:
        if "ready_at" not in {c["name"] for c in insp.get_columns(t)}:
            op.add_column(t, sa.Column("ready_at", sa.DateTime(), nullable=True))
            # existing rows count as ready: the reaper must never delete something it cannot prove failed during build
            op.execute(f"UPDATE {t} SET ready_at = created_at")

def downgrade() -> None:
    pass
