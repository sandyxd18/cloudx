"""vpc peering transit_cidr column

Revision ID: networking_0005
Revises: networking_0004
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "networking_0005"
down_revision = "networking_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "networking_vpc_peerings" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("networking_vpc_peerings")}
        if "transit_cidr" not in cols:
            op.add_column("networking_vpc_peerings", sa.Column("transit_cidr", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "networking_vpc_peerings" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("networking_vpc_peerings")}
        if "transit_cidr" in cols:
            op.drop_column("networking_vpc_peerings", "transit_cidr")
