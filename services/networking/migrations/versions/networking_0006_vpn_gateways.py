"""vpn gateways model and site connections extension

Revision ID: networking_0006
Revises: networking_0005
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "networking_0006"
down_revision = "networking_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = insp.get_table_names()

    if "networking_vpn_gateways" not in tables:
        op.create_table(
            "networking_vpn_gateways",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("router_id", sa.String(), sa.ForeignKey("networking_routers.id"), nullable=False, unique=True),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("host", sa.String(), nullable=False),
            sa.Column("transit_cidr", sa.String(), nullable=False),
            sa.Column("outer_network_id", sa.String(), sa.ForeignKey("networking_networks.id"), nullable=False),
            sa.Column("outer_ip", sa.String(), nullable=False),
            sa.Column("inner_mac", sa.String(), nullable=False),
            sa.Column("outer_mac", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="PENDING_CREATE"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if "networking_vpn_site_connections" in tables:
        cols = {c["name"] for c in insp.get_columns("networking_vpn_site_connections")}
        if "gateway_id" not in cols:
            op.add_column("networking_vpn_site_connections", sa.Column("gateway_id", sa.String(), nullable=True))
        if "status_reason" not in cols:
            op.add_column("networking_vpn_site_connections", sa.Column("status_reason", sa.String(), nullable=True))


def downgrade() -> None:
    # ponytail: drop_table only; the two added columns stay (SQLite cannot drop columns without batch mode)
    if "networking_vpn_gateways" in inspect(op.get_bind()).get_table_names():
        op.drop_table("networking_vpn_gateways")
