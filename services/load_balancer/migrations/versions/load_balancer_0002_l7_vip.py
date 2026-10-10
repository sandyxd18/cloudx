"""VIP details on load balancers, L7 policies and rules

Revision ID: load_balancer_0002
Revises: load_balancer_0001
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "load_balancer_0002"
down_revision = "load_balancer_0001"
branch_labels = None
depends_on = None

LB_COLS = [("vip_mac", sa.String()), ("vip_port_name", sa.String()), ("vip_switch", sa.String()),
           ("vip_prefix", sa.Integer()), ("vip_gateway", sa.String()), ("vip_floating_ip", sa.String()),
           ("host", sa.String())]


def upgrade() -> None:
    insp = inspect(op.get_bind())
    tables = insp.get_table_names()
    have = {c["name"] for c in insp.get_columns("lb_load_balancers")}
    for name, typ in LB_COLS:
        if name not in have:
            op.add_column("lb_load_balancers", sa.Column(name, typ))
    if "lb_l7policies" not in tables:
        op.create_table(
            "lb_l7policies",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("listener_id", sa.String(), sa.ForeignKey("lb_listeners.id"), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("redirect_pool_id", sa.String()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "lb_l7rules" not in tables:
        op.create_table(
            "lb_l7rules",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("policy_id", sa.String(), sa.ForeignKey("lb_l7policies.id"), nullable=False),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("compare_type", sa.String(), nullable=False),
            sa.Column("value", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    pass
