"""initial schema for block_store

Revision ID: block_store_0001
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

revision = "block_store_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "block_store_backups", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("volume_id", sa.String(), nullable=False),
    sa.Column("size_gb", sa.Integer(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("backend", sa.String()),
    sa.Column("object_key", sa.String()),
    sa.Column("project_id", sa.String()),
    sa.Column("error_message", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_block_store_backups_name', 'name'),
    sa.Index('ix_block_store_backups_project_id', 'project_id'),
    sa.Index('ix_block_store_backups_volume_id', 'volume_id'),
)

sa.Table(
    "block_store_cgsnapshots", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("description", sa.String()),
    sa.Column("consistency_group_id", sa.String(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("project_id", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_block_store_cgsnapshots_consistency_group_id', 'consistency_group_id'),
    sa.Index('ix_block_store_cgsnapshots_name', 'name', unique=True),
)

sa.Table(
    "block_store_consistency_groups", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("description", sa.String()),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("project_id", sa.String()),
    sa.Column("volume_ids", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_block_store_consistency_groups_name', 'name', unique=True),
    sa.Index('ix_block_store_consistency_groups_project_id', 'project_id'),
)

sa.Table(
    "block_store_snapshots", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("volume_id", sa.String(), nullable=False),
    sa.Column("size_gb", sa.Integer(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("path", sa.String(), nullable=False),
    sa.Column("backend", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_block_store_snapshots_name', 'name', unique=True),
)

sa.Table(
    "block_store_volume_quotas", metadata,
    sa.Column("project_id", sa.String(), primary_key=True),
    sa.Column("volumes", sa.Integer(), nullable=False),
    sa.Column("gigabytes", sa.Integer(), nullable=False),
    sa.Column("snapshots", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)

sa.Table(
    "block_store_volume_types", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("backend", sa.String(), nullable=False),
    sa.Column("disk_type", sa.String(), nullable=False),
    sa.Column("description", sa.String()),
    sa.Column("max_iops", sa.Integer()),
    sa.Column("max_throughput", sa.Integer()),
    sa.Column("is_public", sa.Boolean(), nullable=False),
    sa.Column("lvm_type", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_block_store_volume_types_name', 'name', unique=True),
)

sa.Table(
    "block_store_volumes", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("size_gb", sa.Integer(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("attached_to_server_id", sa.String()),
    sa.Column("path", sa.String(), nullable=False),
    sa.Column("backend", sa.String()),
    sa.Column("bootable", sa.Boolean(), nullable=False),
    sa.Column("image_id", sa.String()),
    sa.Column("snapshot_id", sa.String()),
    sa.Column("source_volume_id", sa.String()),
    sa.Column("source_type", sa.String()),
    sa.Column("target_device", sa.String()),
    sa.Column("project_id", sa.String()),
    sa.Column("volume_type", sa.String()),
    sa.Column("error_message", sa.String()),
    sa.Column("multiattach", sa.Boolean(), nullable=False),
    sa.Column("replication_status", sa.String(), nullable=False),
    sa.Column("replication_target", sa.String()),
    sa.Column("replication_mode", sa.String()),
    sa.Column("last_replicated_at", sa.DateTime()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_block_store_volumes_name', 'name', unique=True),
    sa.Index('ix_block_store_volumes_project_id', 'project_id'),
)

sa.Table(
    "block_store_snapshot_schedules", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("volume_id", sa.String(), nullable=False),
    sa.Column("interval_seconds", sa.Integer(), nullable=False),
    sa.Column("retention_count", sa.Integer(), nullable=False),
    sa.Column("last_run_at", sa.DateTime()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['volume_id'], ['block_store_volumes.id']),
)


class _Bound:
    """Lets the legacy helper below run unchanged against the migration's own connection."""

    def __init__(self, bind):
        self.bind = bind

    @contextmanager
    def begin(self):
        yield self.bind

def _legacy_ensure_columns(engine) -> None:
    """Add any missing columns to existing tables (safe migration)."""
    inspector = inspect(engine.bind)
    tables = inspector.get_table_names()

    if "block_store_volumes" in tables:
        columns = {col["name"] for col in inspector.get_columns("block_store_volumes")}
        with engine.begin() as conn:
            for col, ddl in (
                ("bootable",         "BOOLEAN DEFAULT FALSE"),
                ("image_id",         "VARCHAR"),
                ("snapshot_id",      "VARCHAR"),
                ("source_volume_id", "VARCHAR"),
                ("source_type",      "VARCHAR"),
                ("target_device",    "VARCHAR"),
                ("project_id",       "VARCHAR"),
                ("volume_type",      "VARCHAR"),
                ("backend",          "VARCHAR DEFAULT 'lvm'"),
                ("error_message",    "VARCHAR"),
                ("multiattach",      "BOOLEAN DEFAULT FALSE"),
                ("replication_status", "VARCHAR DEFAULT 'disabled'"),
                ("replication_target", "VARCHAR"),
                ("replication_mode",   "VARCHAR DEFAULT 'async'"),
                ("last_replicated_at", "TIMESTAMP"),
            ):
                if col not in columns:
                    conn.execute(text(f"ALTER TABLE block_store_volumes ADD COLUMN {col} {ddl}"))
            conn.execute(text("UPDATE block_store_volumes SET bootable = FALSE WHERE bootable IS NULL"))
            conn.execute(text("UPDATE block_store_volumes SET multiattach = FALSE WHERE multiattach IS NULL"))

    if "block_store_snapshots" in tables:
        columns = {col["name"] for col in inspector.get_columns("block_store_snapshots")}
        with engine.begin() as conn:
            if "backend" not in columns:
                conn.execute(text("ALTER TABLE block_store_snapshots ADD COLUMN backend VARCHAR DEFAULT 'lvm'"))

    if "block_store_volume_types" in tables:
        columns = {col["name"] for col in inspector.get_columns("block_store_volume_types")}
        with engine.begin() as conn:
            for col, ddl in (
                ("disk_type",      "VARCHAR DEFAULT 'sata_ssd'"),
                ("max_iops",       "INTEGER"),
                ("max_throughput", "INTEGER"),
                ("lvm_type",       "VARCHAR DEFAULT 'default'"),
            ):
                if col not in columns:
                    conn.execute(text(f"ALTER TABLE block_store_volume_types ADD COLUMN {col} {ddl}"))
            conn.execute(text(
                "DELETE FROM block_store_volume_types "
                "WHERE name IN ('lvm', 'ceph') AND disk_type IS NULL"
            ))


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    _legacy_ensure_columns(_Bound(bind))
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
