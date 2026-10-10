"""per-project role assignments

Revision ID: auth_0002
Revises: auth_0001
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "auth_0002"
down_revision = "auth_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "auth_role_assignments" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "auth_role_assignments",
        sa.Column("user_id", sa.String(), sa.ForeignKey("auth_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("auth_projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("auth_role_assignments")
