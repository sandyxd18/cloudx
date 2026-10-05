"""Alembic environment shared by every CloudX service.

Driven by `cloudx-db-migrate` (shared/migrate.py), which sets, per service, through `config.attributes`:
  url              database URL
  version_table    alembic_version_<service>
  target_metadata  the service's SQLAlchemy metadata (only needed for `--autogenerate`)
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

url = config.attributes["url"]
version_table = config.attributes["version_table"]
target_metadata = config.attributes.get("target_metadata")


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, version_table=version_table,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          version_table=version_table, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
