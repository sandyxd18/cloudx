"""trusts: delegated roles in one project from a trustor to a trustee

Revision ID: auth_0009
Revises: auth_0008
Create Date: 2026-11-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "auth_0009"
down_revision = "auth_0008"
branch_labels = None
depends_on = None

TABLE = "auth_trusts"


def upgrade() -> None:
    if TABLE in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("trustor_user_id", sa.String(), sa.ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trustee_user_id", sa.String(), sa.ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("auth_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("roles", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("remaining_uses", sa.Integer(), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_auth_trusts_trustor_user_id", TABLE, ["trustor_user_id"])
    op.create_index("ix_auth_trusts_trustee_user_id", TABLE, ["trustee_user_id"])


def downgrade() -> None:
    if TABLE in inspect(op.get_bind()).get_table_names():
        op.drop_table(TABLE)
