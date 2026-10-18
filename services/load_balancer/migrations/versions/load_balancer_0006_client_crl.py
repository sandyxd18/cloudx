"""listener client CRL (Octavia client_crl_container_ref)

Revision ID: load_balancer_0006
Revises: load_balancer_0005
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "load_balancer_0006"
down_revision = "load_balancer_0005"
branch_labels = None
depends_on = None

def upgrade() -> None:
    have = {c["name"] for c in inspect(op.get_bind()).get_columns("lb_listeners")}
    if "client_crl_pem" not in have:
        op.add_column("lb_listeners", sa.Column("client_crl_pem", sa.String(), nullable=True))

def downgrade() -> None:
    pass
