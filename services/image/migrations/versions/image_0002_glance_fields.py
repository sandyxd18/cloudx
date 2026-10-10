"""glance fields: min_ram, protected, virtual_size, os_hash_*, properties

Revision ID: image_0002
Revises: image_0001
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "image_0002"
down_revision = "image_0001"
branch_labels = None
depends_on = None

COLUMNS = (
    ("min_ram_mb", sa.Integer(), "0"),
    ("protected", sa.Boolean(), sa.false()),
    ("virtual_size", sa.Integer(), None),
    ("os_hash_algo", sa.String(), None),
    ("os_hash_value", sa.String(), None),
    ("properties", sa.Text(), None),
)


def upgrade() -> None:
    have = {c["name"] for c in inspect(op.get_bind()).get_columns("image_images")}
    for name, type_, default in COLUMNS:
        if name not in have:
            op.add_column("image_images", sa.Column(name, type_, nullable=default is None, server_default=default))


def downgrade() -> None:
    for name, _, _ in COLUMNS:
        op.drop_column("image_images", name)
