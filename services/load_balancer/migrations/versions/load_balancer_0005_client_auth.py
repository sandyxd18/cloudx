"""listener client certificates (Octavia client_authentication / client_ca)

Revision ID: load_balancer_0005
Revises: load_balancer_0004
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "load_balancer_0005"
down_revision = "load_balancer_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in inspect(op.get_bind()).get_columns("lb_listeners")}
    if "client_authentication" not in have:
        op.add_column("lb_listeners", sa.Column("client_authentication", sa.String(), nullable=False, server_default="NONE"))
    if "client_ca_pem" not in have:
        op.add_column("lb_listeners", sa.Column("client_ca_pem", sa.String(), nullable=True))


def downgrade() -> None:
    pass
