"""["db_instances"].ready_at

Revision ID: database_0003
Revises: database_0002
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "database_0003"
down_revision = "database_0002"
branch_labels = None
depends_on = None

TABLES = ["db_instances"]

def upgrade() -> None:
    insp = inspect(op.get_bind())
    for t in TABLES:
        if "ready_at" not in {c["name"] for c in insp.get_columns(t)}:
            op.add_column(t, sa.Column("ready_at", sa.DateTime(), nullable=True))
            # existing rows count as ready: the reaper must never delete something it cannot prove failed during build
            op.execute(f"UPDATE {t} SET ready_at = created_at")

def downgrade() -> None:
    pass
