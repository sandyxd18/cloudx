"""Schema helpers shared by the Alembic migrations and by service startup.

Services no longer create or alter tables themselves. `cloudx-db-migrate` (shared/migrate.py) does, and every
service refuses to start against a database that was never migrated.
"""
from sqlalchemy import inspect, text


def version_table(service: str) -> str:
    """Each service keeps its own Alembic history, so services can share one database or use separate ones."""
    return f"alembic_version_{service}"


def require_schema(engine, service: str) -> None:
    """Fail fast with an actionable message instead of 500s from missing tables."""
    if not inspect(engine).has_table(version_table(service)):
        raise RuntimeError(
            f"{service}: database schema is not initialised. "
            f"Run `cloudx-db-migrate {service}` (or `make migrate`) before starting the service."
        )


def add_missing_columns(bind, metadata) -> None:
    """Add columns that exist in `metadata` but not in the live table (databases built by older releases).

    Columns are added nullable, with the server default if the table definition has one. Tables that do not
    exist are left alone; `metadata.create_all(checkfirst=True)` handles those.
    """
    insp = inspect(bind)
    quote = bind.dialect.identifier_preparer.quote
    for table in metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl = f"ALTER TABLE {quote(table.name)} ADD COLUMN {quote(col.name)} {col.type.compile(dialect=bind.dialect)}"
            if col.server_default is not None:
                ddl += f" DEFAULT {col.server_default.arg.text if hasattr(col.server_default.arg, 'text') else repr(col.server_default.arg)}"
            bind.execute(text(ddl))
