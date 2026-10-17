"""L7 parity: redirect_url/prefix/http_code, admin_state_up on policies; key, invert, admin_state_up on rules

Revision ID: load_balancer_0004
Revises: load_balancer_0003
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "load_balancer_0004"
down_revision = "load_balancer_0003"
branch_labels = None
depends_on = None

def _add(table: str, col: sa.Column) -> None:
    if col.name not in {c["name"] for c in inspect(op.get_bind()).get_columns(table)}:
        op.add_column(table, col)

def upgrade() -> None:
    t = sa.true()
    for c in (sa.Column("redirect_url", sa.String()), sa.Column("redirect_prefix", sa.String()),
              sa.Column("redirect_http_code", sa.Integer()),
              sa.Column("admin_state_up", sa.Boolean(), nullable=False, server_default=t)):
        _add("lb_l7policies", c)
    for c in (sa.Column("key", sa.String()),
              sa.Column("invert", sa.Boolean(), nullable=False, server_default=sa.false()),
              sa.Column("admin_state_up", sa.Boolean(), nullable=False, server_default=t)):
        _add("lb_l7rules", c)

def downgrade() -> None:
    pass