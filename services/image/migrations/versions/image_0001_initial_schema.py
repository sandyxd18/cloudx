"""initial schema for image

Revision ID: image_0001
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

revision = "image_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "image_images", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("filename", sa.String(), nullable=False),
    sa.Column("storage_backend", sa.String(), nullable=False),
    sa.Column("object_name", sa.String(), nullable=False),
    sa.Column("disk_format", sa.String(), nullable=False),
    sa.Column("container_format", sa.String(), nullable=False),
    sa.Column("size", sa.Integer(), nullable=False),
    sa.Column("min_disk_gb", sa.Integer(), nullable=False),
    sa.Column("checksum", sa.String(), nullable=False),
    sa.Column("signature", sa.String()),
    sa.Column("signature_hash_method", sa.String()),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("visibility", sa.String(), nullable=False),
    sa.Column("project_id", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_image_images_name', 'name', unique=True),
    sa.Index('ix_image_images_project_id', 'project_id'),
)

sa.Table(
    "image_members", metadata,
    sa.Column("image_id", sa.String(), primary_key=True),
    sa.Column("member_id", sa.String(), primary_key=True),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)

sa.Table(
    "image_tags", metadata,
    sa.Column("image_id", sa.String(), primary_key=True),
    sa.Column("tag", sa.String(), primary_key=True),
)

sa.Table(
    "image_tasks", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("type", sa.String(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("input", sa.Text()),
    sa.Column("result", sa.Text()),
    sa.Column("message", sa.String()),
    sa.Column("owner", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
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
    if "image_images" in table_names:
        columns = {column["name"] for column in inspector.get_columns("image_images")}
        with engine.begin() as conn:
            if "storage_backend" not in columns:
                conn.execute(text("ALTER TABLE image_images ADD COLUMN storage_backend VARCHAR DEFAULT 'file'"))
            if "object_name" not in columns:
                conn.execute(text("ALTER TABLE image_images ADD COLUMN object_name VARCHAR"))
                conn.execute(text("UPDATE image_images SET object_name = filename WHERE object_name IS NULL"))
            if "visibility" not in columns:
                conn.execute(text("ALTER TABLE image_images ADD COLUMN visibility VARCHAR DEFAULT 'private'"))
            if "project_id" not in columns:
                conn.execute(text("ALTER TABLE image_images ADD COLUMN project_id VARCHAR"))
            conn.execute(text("UPDATE image_images SET storage_backend = 'file' WHERE storage_backend IS NULL"))
            conn.execute(text("UPDATE image_images SET visibility = 'private' WHERE visibility IS NULL"))
            conn.execute(text("UPDATE image_images SET object_name = filename WHERE object_name IS NULL"))


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    _legacy_ensure_columns(_Bound(bind))
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
