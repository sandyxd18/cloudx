"""bgp speakers and learned routes models and tables

Revision ID: networking_0007
Revises: networking_0006
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "networking_0007"
down_revision = "networking_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = insp.get_table_names()

    if "networking_bgp_speakers" not in tables:
        op.create_table(
            "networking_bgp_speakers",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("router_id", sa.String(), sa.ForeignKey("networking_routers.id"), nullable=False, unique=True),
            sa.Column("project_id", sa.String(), nullable=False, index=True),
            sa.Column("host", sa.String(), nullable=False),
            sa.Column("outer_network_id", sa.String(), sa.ForeignKey("networking_networks.id"), nullable=False),
            sa.Column("outer_ip", sa.String(), nullable=False),
            sa.Column("outer_mac", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="down"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if "networking_bgp_learned_routes" not in tables:
        op.create_table(
            "networking_bgp_learned_routes",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("peer_id", sa.String(), nullable=False, index=True),
            sa.Column("prefix", sa.String(), nullable=False),
            sa.Column("nexthop", sa.String(), nullable=False),
            sa.UniqueConstraint("peer_id", "prefix", name="uq_networking_bgp_learned_routes_peer_prefix"),
        )

    if "networking_bgp_peers" in tables:
        cols = {c["name"] for c in insp.get_columns("networking_bgp_peers")}
        if "speaker_id" not in cols:
            op.add_column("networking_bgp_peers", sa.Column("speaker_id", sa.String(), nullable=True))
        if "status_reason" not in cols:
            op.add_column("networking_bgp_peers", sa.Column("status_reason", sa.String(), nullable=True))


def downgrade() -> None:
    # ponytail: drop_table only; the two added columns stay (SQLite cannot drop columns without batch mode)
    insp = inspect(op.get_bind())
    tables = insp.get_table_names()
    if "networking_bgp_learned_routes" in tables:
        op.drop_table("networking_bgp_learned_routes")
    if "networking_bgp_speakers" in tables:
        op.drop_table("networking_bgp_speakers")
