"""alarm history, evaluation periods, statistic

Revision ID: telemetry_0003
Revises: telemetry_0002
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "telemetry_0003"
down_revision = "telemetry_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = inspect(bind).get_table_names()
    if "telemetry_alarm_history" not in tables:
        op.create_table(
            "telemetry_alarm_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("alarm_id", sa.String(36), sa.ForeignKey("telemetry_alarms.id", ondelete="CASCADE"), nullable=False),
            sa.Column("old_state", sa.String(24), nullable=False),
            sa.Column("new_state", sa.String(24), nullable=False),
            sa.Column("reason", sa.String(255), nullable=True),
            sa.Column("value", sa.Float(), nullable=True),
            sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_telemetry_alarm_history_alarm_id", "telemetry_alarm_history", ["alarm_id"])
        op.create_index("ix_telemetry_alarm_history_at", "telemetry_alarm_history", ["at"])

    if "telemetry_alarms" in tables:
        cols = {c["name"] for c in inspect(bind).get_columns("telemetry_alarms")}
        if "evaluation_periods" not in cols:
            op.add_column("telemetry_alarms", sa.Column("evaluation_periods", sa.Integer(), nullable=False, server_default="1"))
        if "statistic" not in cols:
            op.add_column("telemetry_alarms", sa.Column("statistic", sa.String(16), nullable=False, server_default="avg"))
        if "breach_count" not in cols:
            op.add_column("telemetry_alarms", sa.Column("breach_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    bind = op.get_bind()
    tables = inspect(bind).get_table_names()
    if "telemetry_alarm_history" in tables:
        op.drop_table("telemetry_alarm_history")
    if "telemetry_alarms" in tables:
        cols = {c["name"] for c in inspect(bind).get_columns("telemetry_alarms")}
        if "breach_count" in cols:
            op.drop_column("telemetry_alarms", "breach_count")
        if "statistic" in cols:
            op.drop_column("telemetry_alarms", "statistic")
        if "evaluation_periods" in cols:
            op.drop_column("telemetry_alarms", "evaluation_periods")
