"""network mtu, port allowed_address_pairs, router routes

Revision ID: networking_0003
Revises: networking_0002
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "networking_0003"
down_revision = "networking_0002"
branch_labels = None
depends_on = None

COLUMNS = (
    ("networking_networks", "mtu", sa.Integer()),
    ("networking_ports", "allowed_address_pairs", sa.Text()),
    ("networking_routers", "routes", sa.Text()),
)


def upgrade() -> None:
    insp = inspect(op.get_bind())
    for table, name, type_ in COLUMNS:
        if name not in {c["name"] for c in insp.get_columns(table)}:
            op.add_column(table, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for table, name, _ in COLUMNS:
        op.drop_column(table, name)
