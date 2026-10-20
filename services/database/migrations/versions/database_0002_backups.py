"""db_backups

Revision ID: database_0002
Revises: database_0001
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "database_0002"
down_revision = "database_0001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    if "db_backups" not in set(inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "db_backups",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("instance_id", sa.String(), nullable=True),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("engine", sa.String(), nullable=False),
            sa.Column("size_gb", sa.Integer(), nullable=False),
            sa.Column("volume_backup_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_db_backups_project_id", "db_backups", ["project_id"])

def downgrade() -> None:
    pass
