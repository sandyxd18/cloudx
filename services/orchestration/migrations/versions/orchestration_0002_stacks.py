"""Heat-like stacks: stacks, stack resources, stack events

Revision ID: orchestration_0002
Revises: orchestration_0001
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "orchestration_0002"
down_revision = "orchestration_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = inspect(op.get_bind()).get_table_names()
    ts = lambda: sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)  # noqa: E731
    if "orchestration_stacks" not in tables:
        op.create_table(
            "orchestration_stacks",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("project_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("status_reason", sa.String(255)),
            sa.Column("template", sa.Text(), nullable=False),
            sa.Column("parameters", sa.Text(), nullable=False),
            sa.Column("outputs", sa.Text(), nullable=False),
            sa.Column("disable_rollback", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            ts(),
        )
        op.create_index("ix_orchestration_stacks_project_id", "orchestration_stacks", ["project_id"])
    if "orchestration_stack_resources" not in tables:
        op.create_table(
            "orchestration_stack_resources",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("stack_id", sa.String(64), sa.ForeignKey("orchestration_stacks.id"), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("type", sa.String(64), nullable=False),
            sa.Column("physical_id", sa.String(128)),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("status_reason", sa.String(255)),
            sa.Column("properties", sa.Text(), nullable=False),
            sa.Column("attributes", sa.Text(), nullable=False),
            ts(),
        )
        op.create_index("ix_orchestration_stack_resources_stack_id", "orchestration_stack_resources", ["stack_id"])
    if "orchestration_stack_events" not in tables:
        op.create_table(
            "orchestration_stack_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("stack_id", sa.String(64), nullable=False),
            sa.Column("resource_name", sa.String(128)),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("reason", sa.String(255)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_orchestration_stack_events_stack_id", "orchestration_stack_events", ["stack_id"])


def downgrade() -> None:
    pass
