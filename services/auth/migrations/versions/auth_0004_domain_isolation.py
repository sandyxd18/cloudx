"""domain isolation: domain_id on projects and users, is_domain_admin on users

Revision ID: auth_0004
Revises: auth_0003
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "auth_0004"
down_revision = "auth_0003"
branch_labels = None
depends_on = None

def _cols(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}

def upgrade() -> None:
    # the old bootstrap may already have added domain_id on live installs
    for table in ("auth_projects", "auth_users"):
        if "domain_id" not in _cols(table):
            with op.batch_alter_table(table) as b:
                b.add_column(sa.Column("domain_id", sa.String(), nullable=False, server_default="default"))
                b.create_foreign_key(f"fk_{table}_domain", "auth_domains", ["domain_id"], ["id"])
        else:
            op.execute(sa.text(f"UPDATE {table} SET domain_id = 'default' WHERE domain_id IS NULL"))
    if "is_domain_admin" not in _cols("auth_users"):
        op.add_column("auth_users", sa.Column("is_domain_admin", sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade() -> None:
    with op.batch_alter_table("auth_users") as b:
        b.drop_column("is_domain_admin")
        b.drop_column("domain_id")
    with op.batch_alter_table("auth_projects") as b:
        b.drop_column("domain_id")
