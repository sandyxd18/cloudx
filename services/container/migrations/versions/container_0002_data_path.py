"""add ct_containers.data_path

Revision ID: container_0002
Revises: container_0001
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "container_0002"
down_revision = "container_0001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("ct_containers")}
    if "data_path" not in cols:
        op.add_column("ct_containers", sa.Column("data_path", sa.String(), nullable=True))

def downgrade() -> None:
    pass