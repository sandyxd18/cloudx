"""networking quota columns on auth_project_quotas

Revision ID: auth_0003
Revises: auth_0002
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "auth_0003"
down_revision = "auth_0002"
branch_labels = None
depends_on = None

COLUMNS = {
    "max_networks": 100, "max_subnets": 100, "max_ports": 500,
    "max_routers": 10, "max_floating_ips": 50, "max_security_groups": 20,
}


def upgrade() -> None:
    have = {c["name"] for c in inspect(op.get_bind()).get_columns("auth_project_quotas")}
    for name, default in COLUMNS.items():
        if name not in have:
            op.add_column("auth_project_quotas",
                          sa.Column(name, sa.Integer(), nullable=False, server_default=str(default)))


def downgrade() -> None:
    for name in COLUMNS:
        op.drop_column("auth_project_quotas", name)
