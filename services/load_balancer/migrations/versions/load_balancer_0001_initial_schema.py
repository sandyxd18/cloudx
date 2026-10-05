"""initial schema for load_balancer

Revision ID: load_balancer_0001
Revises:
Create Date: 2026-10-05

Idempotent baseline. On a fresh database it creates every table. On a database built by an older release (when the
service still ran create_all()/ensure_columns() itself) it creates what is missing, adds missing columns,
so `cloudx-db-migrate` is safe to run against any existing install.

The table definitions are frozen here on purpose: never edit them, add a new revision instead.
"""
from alembic import op
import sqlalchemy as sa
from shared.schema import add_missing_columns

revision = "load_balancer_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "lb_load_balancers", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("lb_type", sa.String(), nullable=False),
    sa.Column("vip_address", sa.String()),
    sa.Column("vip_network_id", sa.String()),
    sa.Column("vip_port_id", sa.String()),
    sa.Column("vip_floating_ip_id", sa.String()),
    sa.Column("provisioning_status", sa.String(), nullable=False),
    sa.Column("operating_status", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_lb_load_balancers_name', 'name'),
    sa.Index('ix_lb_load_balancers_project_id', 'project_id'),
)

sa.Table(
    "lb_certificates", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("load_balancer_id", sa.String(), nullable=False),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("certificate_pem", sa.String(), nullable=False),
    sa.Column("private_key_pem", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['load_balancer_id'], ['lb_load_balancers.id']),
)

sa.Table(
    "lb_listeners", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("load_balancer_id", sa.String(), nullable=False),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("protocol", sa.String(), nullable=False),
    sa.Column("protocol_port", sa.Integer(), nullable=False),
    sa.Column("default_pool_id", sa.String()),
    sa.Column("certificate_id", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['load_balancer_id'], ['lb_load_balancers.id']),
)

sa.Table(
    "lb_pools", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("load_balancer_id", sa.String(), nullable=False),
    sa.Column("listener_id", sa.String(), nullable=False),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("protocol", sa.String(), nullable=False),
    sa.Column("lb_algorithm", sa.String(), nullable=False),
    sa.Column("server_group_id", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['listener_id'], ['lb_listeners.id']),
    sa.ForeignKeyConstraint(['load_balancer_id'], ['lb_load_balancers.id']),
)

sa.Table(
    "lb_health_monitors", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("pool_id", sa.String(), nullable=False),
    sa.Column("type", sa.String(), nullable=False),
    sa.Column("delay", sa.Integer(), nullable=False),
    sa.Column("timeout", sa.Integer(), nullable=False),
    sa.Column("max_retries", sa.Integer(), nullable=False),
    sa.Column("url_path", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['pool_id'], ['lb_pools.id']),
)

sa.Table(
    "lb_members", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("pool_id", sa.String(), nullable=False),
    sa.Column("instance_id", sa.String()),
    sa.Column("address", sa.String(), nullable=False),
    sa.Column("protocol_port", sa.Integer(), nullable=False),
    sa.Column("weight", sa.Integer(), nullable=False),
    sa.Column("operating_status", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['pool_id'], ['lb_pools.id']),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
