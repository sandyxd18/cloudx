"""project names and usernames unique per domain instead of globally

Revision ID: auth_0005
Revises: auth_0004
Create Date: 2026-10-17
"""
from alembic import op

revision = "auth_0005"
down_revision = "auth_0004"
branch_labels = None
depends_on = None

_T = (("auth_projects", "name", "uq_auth_projects_domain_name"), ("auth_users", "username", "uq_auth_users_domain_username"))


def upgrade() -> None:
    for table, col, uq in _T:
        ix = f"ix_{table}_{col}"
        with op.batch_alter_table(table) as b:
            b.drop_index(ix)
            b.create_index(ix, [col], unique=False)
            b.create_unique_constraint(uq, ["domain_id", col])


def downgrade() -> None:
    for table, col, uq in _T:
        ix = f"ix_{table}_{col}"
        with op.batch_alter_table(table) as b:
            b.drop_constraint(uq, type_="unique")
            b.drop_index(ix)
            b.create_index(ix, [col], unique=True)
