"""initial schema for billing

Revision ID: billing_0001
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

revision = "billing_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "billing_invoices", metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("invoice_number", sa.String(32), nullable=False),
    sa.Column("project_id", sa.String(64), nullable=False),
    sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("subtotal", sa.Numeric(15, 4), nullable=False),
    sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False),
    sa.Column("tax_amount", sa.Numeric(15, 4), nullable=False),
    sa.Column("total", sa.Numeric(15, 4), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("notes", sa.Text()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint('invoice_number'),
    sa.Index('ix_billing_invoices_project_id', 'project_id'),
)

sa.Table(
    "billing_rating_sheets", metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("resource_type", sa.String(32), nullable=False),
    sa.Column("unit", sa.String(32), nullable=False),
    sa.Column("rate_per_unit", sa.Numeric(15, 4), nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("description", sa.String(255)),
    sa.Column("is_active", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index('ix_billing_rating_sheets_resource_type', 'resource_type'),
)

sa.Table(
    "billing_usage_records", metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("project_id", sa.String(64), nullable=False),
    sa.Column("resource_type", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(64)),
    sa.Column("quantity", sa.Numeric(15, 4), nullable=False),
    sa.Column("unit", sa.String(32), nullable=False),
    sa.Column("rate_per_unit", sa.Numeric(15, 4), nullable=False),
    sa.Column("total_cost", sa.Numeric(15, 4), nullable=False),
    sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index('ix_billing_usage_records_project_id', 'project_id'),
    sa.Index('ix_billing_usage_records_resource_id', 'resource_id'),
    sa.Index('ix_billing_usage_records_resource_type', 'resource_type'),
)

sa.Table(
    "billing_invoice_items", metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("invoice_id", sa.Integer(), nullable=False),
    sa.Column("resource_type", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(64)),
    sa.Column("description", sa.String(255)),
    sa.Column("quantity", sa.Numeric(15, 4), nullable=False),
    sa.Column("unit", sa.String(32), nullable=False),
    sa.Column("rate_per_unit", sa.Numeric(15, 4), nullable=False),
    sa.Column("total", sa.Numeric(15, 4), nullable=False),
    sa.ForeignKeyConstraint(['invoice_id'], ['billing_invoices.id']),
)


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
