"""backup schedule: db_instances.backup_every_hours/backup_keep, db_backups.scheduled

Revision ID: database_0004
Revises: database_0003
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "database_0004"
down_revision = "database_0003"
branch_labels = None
depends_on = None

COLS = {
    "db_instances": [sa.Column("backup_every_hours", sa.Integer(), nullable=True), sa.Column("backup_keep", sa.Integer(), nullable=True)],
    "db_backups": [sa.Column("scheduled", sa.Boolean(), nullable=False, server_default=sa.false())],
}

def upgrade() -> None:
    insp = inspect(op.get_bind())
    for table, cols in COLS.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for c in cols:
            if c.name not in have:
                op.add_column(table, c)

def downgrade() -> None:
    pass
