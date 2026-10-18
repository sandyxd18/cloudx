"""dns quota columns (zones, recordsets) on the project and domain quota tables

Revision ID: auth_0007
Revises: auth_0006
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "auth_0007"
down_revision = "auth_0006"
branch_labels = None
depends_on = None

PROJECT = {"max_zones": 10, "max_recordsets": 200}

def upgrade() -> None:
    insp = inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("auth_project_quotas")}
    for name, default in PROJECT.items():
        if name not in have:
            op.add_column("auth_project_quotas", sa.Column(name, sa.Integer(), nullable=False, server_default=str(default)))
    have = {c["name"] for c in insp.get_columns("auth_domain_quotas")}
    for name in PROJECT:
        if name not in have:
            op.add_column("auth_domain_quotas", sa.Column(name, sa.Integer(), nullable=True))

def downgrade() -> None:
    pass