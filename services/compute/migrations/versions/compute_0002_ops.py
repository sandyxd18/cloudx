"""flavor extra_specs, instance actions, compute services

Revision ID: compute_0002
Revises: compute_0001
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "compute_0002"
down_revision = "compute_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = insp.get_table_names()
    if "compute_flavors" in tables and "extra_specs" not in {c["name"] for c in insp.get_columns("compute_flavors")}:
        op.add_column("compute_flavors", sa.Column("extra_specs", sa.Text()))
    if "compute_server_actions" not in tables:
        op.create_table(
            "compute_server_actions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("server_id", sa.String(), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("user_id", sa.String()),
            sa.Column("project_id", sa.String()),
            sa.Column("start_time", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_compute_server_actions_server_id", "compute_server_actions", ["server_id"])
    if "compute_services" not in tables:
        op.create_table(
            "compute_services",
            sa.Column("host", sa.String(), primary_key=True),
            sa.Column("disabled", sa.Boolean(), nullable=False),
            sa.Column("disabled_reason", sa.String()),
            sa.Column("forced_down", sa.Boolean(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    pass
