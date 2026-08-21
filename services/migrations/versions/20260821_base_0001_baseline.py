"""baseline — existing CloudX schema (Fase 1-4)

Revision ID: base_0001
Revises:
Create Date: 2026-08-21

This is an EMPTY baseline. The live schema created by each service's
`Base.metadata.create_all()` + `ensure_columns()` at startup IS the baseline.

Adopt it on an existing database with:

    alembic -c services/migrations/alembic.ini stamp base_0001

New schema changes should be added as fresh revisions on top of this one,
following the additive-first / zero-downtime rules in README.md.
"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "base_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Intentionally empty — baseline reflects the pre-Alembic schema.
    pass


def downgrade() -> None:
    # No downgrade from baseline.
    pass
