"""hourly rollups and threshold alarms

Revision ID: telemetry_0002
Revises: telemetry_0001
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "telemetry_0002"
down_revision = "telemetry_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = inspect(op.get_bind()).get_table_names()
    if "telemetry_rollups" not in tables:
        op.create_table(
            "telemetry_rollups",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("instance_id", sa.String(64), nullable=False),
            sa.Column("project_id", sa.String(64)),
            sa.Column("metric_type", sa.String(32), nullable=False),
            sa.Column("bucket", sa.DateTime(timezone=True), nullable=False),
            sa.Column("avg_value", sa.Float(), nullable=False),
            sa.Column("min_value", sa.Float(), nullable=False),
            sa.Column("max_value", sa.Float(), nullable=False),
            sa.Column("samples", sa.Integer(), nullable=False),
        )
        for c in ("instance_id", "project_id", "metric_type", "bucket"):
            op.create_index(f"ix_telemetry_rollups_{c}", "telemetry_rollups", [c])
    if "telemetry_alarms" not in tables:
        op.create_table(
            "telemetry_alarms",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("metric_type", sa.String(32), nullable=False),
            sa.Column("instance_id", sa.String(64)),
            sa.Column("comparison", sa.String(2), nullable=False),
            sa.Column("threshold", sa.Float(), nullable=False),
            sa.Column("period", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("state", sa.String(24), nullable=False),
            sa.Column("state_reason", sa.String(255)),
            sa.Column("state_updated_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_telemetry_alarms_project_id", "telemetry_alarms", ["project_id"])


def downgrade() -> None:
    op.drop_table("telemetry_alarms")
    op.drop_table("telemetry_rollups")
