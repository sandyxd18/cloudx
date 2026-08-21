# ═══════════════════════════════════════════════════════════════════════════════
# CloudX shared Alembic scaffold (Fase 5)
# ═══════════════════════════════════════════════════════════════════════════════
# CloudX services currently create their schema at startup via
# `Base.metadata.create_all()` + idempotent `ensure_columns()`. Fase 5 introduces
# Alembic so schema changes become versioned, reviewable, and applicable WITHOUT
# taking the API down (additive-first migrations).
#
# This scaffold is intentionally shared: every service writes to the SAME
# PostgreSQL database, so a single Alembic history tracks all cloudx_* tables.
#
# ── One-time adoption of the existing schema ───────────────────────────────────
# The current live schema is the baseline. Stamp it so Alembic knows the DB is
# already at the baseline revision and does NOT try to recreate tables:
#
#   alembic -c services/migrations/alembic.ini stamp base_0001
#
# ── Creating a new migration (additive, zero-downtime) ─────────────────────────
#   alembic -c services/migrations/alembic.ini revision -m "add server description"
#   # edit the generated file: ADD COLUMN ... (nullable / with default)
#   alembic -c services/migrations/alembic.ini upgrade head
#
# ── Rules for zero-downtime ────────────────────────────────────────────────────
#   1. Add columns as NULLABLE or with a DEFAULT (never NOT NULL in one step).
#   2. Deploy code that writes the new column.
#   3. Backfill data in a separate migration if required.
#   4. Only then add constraints / drop old columns (separate migration).
