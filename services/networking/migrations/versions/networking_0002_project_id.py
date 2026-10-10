"""project_id on subnets, routers, ports and floating IPs

Revision ID: networking_0002
Revises: networking_0001
Create Date: 2026-10-10

NULL = created before ownership existed; such rows stay visible to every project.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "networking_0002"
down_revision = "networking_0001"
branch_labels = None
depends_on = None

TABLES = ("networking_subnets", "networking_routers", "networking_ports", "networking_floating_ips")


def upgrade() -> None:
    insp = inspect(op.get_bind())
    for table in TABLES:
        if "project_id" not in {c["name"] for c in insp.get_columns(table)}:
            op.add_column(table, sa.Column("project_id", sa.String(), nullable=True))
            op.create_index(f"ix_{table}_project_id", table, ["project_id"])


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        op.drop_column(table, "project_id")
