"""initial schema for messaging

Revision ID: messaging_0001
Revises:
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "messaging_0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    have = set(inspect(op.get_bind()).get_table_names())
    if "msg_queues" not in have:
        op.create_table(
            "msg_queues",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("project_id", "name"),
        )
        op.create_index("ix_msg_queues_project_id", "msg_queues", ["project_id"])
    if "msg_messages" not in have:
        op.create_table(
            "msg_messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("queue_id", sa.String(), sa.ForeignKey("msg_queues.id", ondelete="CASCADE"), nullable=False),
            sa.Column("body", sa.JSON(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("claim_id", sa.String(), nullable=True),
            sa.Column("claimed_until", sa.DateTime(), nullable=True),
            sa.Column("deliveries", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_msg_messages_queue_id", "msg_messages", ["queue_id"])
        op.create_index("ix_msg_messages_claim_id", "msg_messages", ["claim_id"])

def downgrade() -> None:
    op.drop_table("msg_messages")
    op.drop_table("msg_queues")
