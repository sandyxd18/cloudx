"""unique name per project and router project_id

Revision ID: networking_0004
Revises: networking_0003
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "networking_0004"
down_revision = "networking_0003"
branch_labels = None
depends_on = None

TABLES = (
    "networking_networks",
    "networking_subnets",
    "networking_routers",
    "networking_ports",
    "networking_floating_ips",
    "networking_vpc_peerings",
    "networking_bgp_peers",
    "networking_security_groups",
    "networking_qos_policies",
    "networking_subnet_pools",
    "networking_trunks",
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if "networking_routers" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("networking_routers")}
        if "project_id" not in cols:
            op.add_column("networking_routers", sa.Column("project_id", sa.String(), nullable=True))
            op.create_index("ix_networking_routers_project_id", "networking_routers", ["project_id"])

    for table in TABLES:
        if table not in insp.get_table_names():
            continue

        indexes = {idx["name"]: idx for idx in insp.get_indexes(table)}
        unique_constraints = {uc["name"]: uc for uc in insp.get_unique_constraints(table)}

        idx_name = f"ix_{table}_name"
        uq_name = f"uq_{table}_project_id_name"

        with op.batch_alter_table(table) as batch_op:
            if idx_name in indexes:
                batch_op.drop_index(idx_name)

            batch_op.create_index(idx_name, ["name"], unique=False)

            if uq_name not in unique_constraints:
                batch_op.create_unique_constraint(uq_name, ["project_id", "name"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    for table in TABLES:
        if table not in insp.get_table_names():
            continue

        idx_name = f"ix_{table}_name"
        uq_name = f"uq_{table}_project_id_name"

        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(uq_name, type_="unique")
            batch_op.drop_index(idx_name)
            batch_op.create_index(idx_name, ["name"], unique=True)
