# CloudX database migrations (Alembic)

Alembic is the **only** thing that creates or alters tables. Services never call `create_all()` or patch columns;
at startup they only check that their schema was migrated (`shared.schema.require_schema`) and fail with a clear
message if it was not.

## Layout

- One history **per service**: `services/<service>/migrations/versions/`.
- One version table per service: `alembic_version_<service>`. Services can therefore share a database (the default
  `cloudx` database holds auth, block_store, compute, image, networking, placement, telemetry, ...) or use separate
  ones (billing, event, key_manager, orchestration and k8s_engine default to their own databases) without colliding.
- `registry.py` says which database each service uses. The URL is resolved exactly as the service resolves it at
  runtime: `/etc/cloudx/<service>/<service>.conf`, then the env vars listed there, then the default.
- `env.py`, `script.py.mako` and `alembic.ini` are shared. Do not call `alembic` directly; use `cloudx-db-migrate`.

## Everyday use

```bash
sudo make migrate                        # control plane: upgrade every service to head (also run by install-systemd.sh)
cloudx-db-migrate compute auth           # only some services
cloudx-db-migrate --current              # revision per service
```

Run migrations **before** starting (or restarting) services after an upgrade. The first revision of every service
(`<service>_0001`) is an idempotent baseline: on a fresh database it creates all tables; on a database built by an
older release it creates what is missing, adds missing columns and runs the old data fix-ups. So the same command
works for new installs and for existing ones, with no manual `stamp` step.

## Changing the schema

1. Edit the model (`services/<service>/app/models.py`, or `main.py` for services not split yet).
2. Generate a revision and review it:

   ```bash
   cloudx-db-migrate --revision compute -m "add server notes" --autogenerate
   ```

3. Commit the model change and the revision together, then `sudo make migrate`.

`tests/test_migrations.py` fails if a service's migrations and its models disagree, so a model change without a
revision cannot be merged by accident.

## Rules for zero-downtime

1. Add columns as NULLABLE or with a DEFAULT (never NOT NULL in one step).
2. Deploy code that writes the new column.
3. Backfill data in a separate migration if required.
4. Only then add constraints / drop old columns (separate migration).

Never edit an existing revision, in particular the frozen table definitions inside `<service>_0001`.

## Notes on older installs

Earlier releases shipped an empty shared `base_0001`/`0002_backups` history in a table called `alembic_version`.
It is no longer used and can be dropped (`DROP TABLE alembic_version`); the per-service baselines cover everything
it covered.
