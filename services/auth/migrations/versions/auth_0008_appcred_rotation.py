"""application credential rotation: previous_secret_hash and previous_valid_until

Revision ID: auth_0008
Revises: auth_0007
Create Date: 2026-11-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "auth_0008"
down_revision = "auth_0007"
branch_labels = None
depends_on = None

TABLE = "auth_application_credentials"

def _cols(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}

def upgrade() -> None:
    have = _cols(TABLE)
    if "previous_secret_hash" not in have:
        op.add_column(TABLE, sa.Column("previous_secret_hash", sa.String(), nullable=True))
    if "previous_valid_until" not in have:
        op.add_column(TABLE, sa.Column("previous_valid_until", sa.DateTime(), nullable=True))

def downgrade() -> None:
    have = _cols(TABLE)
    with op.batch_alter_table(TABLE) as b:
        if "previous_valid_until" in have:
            b.drop_column("previous_valid_until")
        if "previous_secret_hash" in have:
            b.drop_column("previous_secret_hash")
