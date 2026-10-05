"""initial schema for compute

Revision ID: compute_0001
Revises:
Create Date: 2026-10-05

Idempotent baseline. On a fresh database it creates every table. On a database built by an older release (when the
service still ran create_all()/ensure_columns() itself) it creates what is missing, adds missing columns and runs the old data fix-ups,
so `cloudx-db-migrate` is safe to run against any existing install.

The table definitions are frozen here on purpose: never edit them, add a new revision instead.
"""
from contextlib import contextmanager
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from shared.schema import add_missing_columns

revision = "compute_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "compute_aggregates", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("availability_zone", sa.String()),
    sa.Column("extra_metadata", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.Index('ix_compute_aggregates_name', 'name', unique=True),
)

sa.Table(
    "compute_flavors", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("vcpus", sa.Integer(), nullable=False),
    sa.Column("ram_mb", sa.Integer(), nullable=False),
    sa.Column("disk_gb", sa.Integer(), nullable=False),
    sa.Column("ephemeral_gb", sa.Integer(), nullable=False),
    sa.Column("swap_mb", sa.Integer(), nullable=False),
    sa.Column("is_public", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.Index('ix_compute_flavors_name', 'name', unique=True),
)

sa.Table(
    "compute_keypairs", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("public_key", sa.String(), nullable=False),
    sa.Column("fingerprint", sa.String(), nullable=False),
    sa.Column("key_type", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_compute_keypairs_name', 'name', unique=True),
)

sa.Table(
    "compute_server_groups", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("policy", sa.String(), nullable=False),
    sa.Column("project_id", sa.String()),
    sa.Column("members", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_compute_server_groups_name', 'name'),
)

sa.Table(
    "compute_server_tags", metadata,
    sa.Column("server_id", sa.String(), primary_key=True),
    sa.Column("tag", sa.String(), primary_key=True),
)

sa.Table(
    "compute_servers", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("project_id", sa.String()),
    sa.Column("image_id", sa.String(), nullable=False),
    sa.Column("root_volume_id", sa.String()),
    sa.Column("boot_source", sa.String()),
    sa.Column("bootable", sa.Boolean(), nullable=False),
    sa.Column("flavor", sa.String(), nullable=False),
    sa.Column("flavor_name", sa.String()),
    sa.Column("vcpus", sa.Integer()),
    sa.Column("ram_mb", sa.Integer()),
    sa.Column("disk_gb", sa.Integer()),
    sa.Column("network_id", sa.String(), nullable=False),
    sa.Column("port_id", sa.String(), nullable=False),
    sa.Column("extra_port_ids", sa.String()),
    sa.Column("hostname", sa.String()),
    sa.Column("user_data", sa.String()),
    sa.Column("vendor_data", sa.String()),
    sa.Column("keypair_name", sa.String()),
    sa.Column("public_key_snapshot", sa.String()),
    sa.Column("server_group_id", sa.String()),
    sa.Column("backend", sa.String(), nullable=False),
    sa.Column("host", sa.String()),
    sa.Column("instance_dir", sa.String()),
    sa.Column("disk_path", sa.String()),
    sa.Column("config_drive_path", sa.String()),
    sa.Column("libvirt_domain_name", sa.String()),
    sa.Column("tap_name", sa.String()),
    sa.Column("mac_address", sa.String()),
    sa.Column("ip_address", sa.String()),
    sa.Column("error_message", sa.String()),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("locked", sa.Boolean(), nullable=False),
    sa.Column("locked_by", sa.String()),
    sa.Column("shelved_image_id", sa.String()),
    sa.Column("original_flavor", sa.String()),
    sa.Column("original_vcpus", sa.Integer()),
    sa.Column("original_ram_mb", sa.Integer()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_compute_servers_name', 'name', unique=True),
    sa.Index('ix_compute_servers_project_id', 'project_id'),
)

sa.Table(
    "compute_aggregate_hosts", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("aggregate_id", sa.String(), nullable=False),
    sa.Column("host", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['aggregate_id'], ['compute_aggregates.id'], ondelete='CASCADE'),
)


class _Bound:
    """Lets the legacy helper below run unchanged against the migration's own connection."""

    def __init__(self, bind):
        self.bind = bind

    @contextmanager
    def begin(self):
        yield self.bind

def _legacy_ensure_columns(engine):
    inspector = inspect(engine.bind)
    table_names = inspector.get_table_names()
    if "compute_servers" in table_names:
        columns = {column["name"] for column in inspector.get_columns("compute_servers")}
        with engine.begin() as conn:
            if "keypair_name" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN keypair_name VARCHAR"))
            if "public_key_snapshot" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN public_key_snapshot VARCHAR"))
            string_columns = (
                "backend",
                "host",
                "instance_dir",
                "disk_path",
                "config_drive_path",
                "libvirt_domain_name",
                "tap_name",
                "mac_address",
                "ip_address",
                "error_message",
                "flavor_name",
                "hostname",
                "user_data",
                "vendor_data",
                "root_volume_id",
                "boot_source",
                "extra_port_ids",
                "project_id",
                "server_group_id",
                "locked_by",
            )
            for column in string_columns:
                if column not in columns:
                    default = " DEFAULT 'libvirt'" if column == "backend" else ""
                    conn.execute(text(f"ALTER TABLE compute_servers ADD COLUMN {column} VARCHAR{default}"))
            for column in ("vcpus", "ram_mb", "disk_gb"):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE compute_servers ADD COLUMN {column} INTEGER"))
            if "bootable" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN bootable BOOLEAN DEFAULT TRUE"))
            if "locked" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN locked BOOLEAN DEFAULT FALSE"))
            if "shelved_image_id" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN shelved_image_id VARCHAR"))
            if "original_flavor" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN original_flavor VARCHAR"))
            if "original_vcpus" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN original_vcpus INTEGER"))
            if "original_ram_mb" not in columns:
                conn.execute(text("ALTER TABLE compute_servers ADD COLUMN original_ram_mb INTEGER"))
            conn.execute(text("UPDATE compute_servers SET backend = 'libvirt' WHERE backend IS NULL OR backend = 'simulator'"))
            conn.execute(text("UPDATE compute_servers SET flavor_name = flavor WHERE flavor_name IS NULL"))
            conn.execute(text("UPDATE compute_servers SET locked = FALSE WHERE locked IS NULL"))

    if "compute_keypairs" in table_names:
        kp_columns = {column["name"] for column in inspector.get_columns("compute_keypairs")}
        with engine.begin() as conn:
            if "key_type" not in kp_columns:
                conn.execute(text("ALTER TABLE compute_keypairs ADD COLUMN key_type VARCHAR DEFAULT 'rsa'"))
                conn.execute(text("UPDATE compute_keypairs SET key_type = 'rsa' WHERE key_type IS NULL"))

    if "compute_flavors" in table_names:
        flavor_columns = {column["name"] for column in inspector.get_columns("compute_flavors")}
        with engine.begin() as conn:
            for column, ddl in (
                ("ephemeral_gb", "INTEGER DEFAULT 0"),
                ("swap_mb", "INTEGER DEFAULT 0"),
                ("is_public", "BOOLEAN DEFAULT TRUE"),
                ("created_at", "TIMESTAMP"),
                ("updated_at", "TIMESTAMP"),
            ):
                if column not in flavor_columns:
                    conn.execute(text(f"ALTER TABLE compute_flavors ADD COLUMN {column} {ddl}"))


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    _legacy_ensure_columns(_Bound(bind))
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
