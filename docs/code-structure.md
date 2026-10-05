# CloudX Code Structure

This document describes the code layout after the refactor (services split by responsibility, `shared` as an installable
package, database schema managed by Alembic). For the high-level architecture see `architecture.md`.

## Repository layout

```
cli/cloudx_cli/          `cloudx` CLI (Click)
  main.py                entry point (`cloudx_cli.main:cli`), only imports the command modules
  app.py                 root `cli` group
  common.py              config/credentials, token, `_api`, output formatting
  commands/*.py          one module per command group (auth, compute, network, storage, ...)
services/
  shared/                `shared` package (config, auth middleware, rpc, service_client, schema, migrate)
  migrations/            shared Alembic tooling (env.py, registry.py); each service keeps its own history
  <service>/app/         service code (see below)
  <service>/migrations/  migration history for that service
tests/                   pytest: one folder per service (unit) + tests/live (needs a lab); see the Tests section
etc/                     systemd units and sample configuration
scripts/                 installers and lab test scripts
```

## Split API services (auth, compute, block_store, networking)

Each `app/main.py` only creates the FastAPI app, middleware, startup events, health check, and mounts the routers.

| Module | Contents |
|---|---|
| `config.py` | constants read from the environment |
| `db.py` | engine, `SessionLocal`, `DB` dependency |
| `models.py` | SQLAlchemy models |
| `schemas.py` | Pydantic schemas |
| `clients.py` | HTTP/RPC calls to other services (compute, block_store); the `_rpc` state lives here |
| `serializers.py`, `lookups.py`, `service.py` | response conversion, entity lookups, shared business logic |
| `bootstrap.py` | seed data (flavors, quotas, admin, default SG); **not** DDL |
| `routers/*.py` | endpoints, grouped by resource |

Rules to keep in mind:

- Names that tests patch, or whose value is replaced at runtime (for example `clients._rpc`, `clients.api_get`,
  `ovn_cli.ovn`), are called through their module (`clients.api_get(...)`) and not imported by name.
- The other services (image, placement, billing, and so on) are still a single `main.py`; the same pattern applies if
  they are split later.

## Host-addressed RPC (multi-node)

`shared.rpc.host_queue(service, host)` gives the routing key of one node (`compute.<host>`, `block-store.<host>`; just
`service` when the host is unknown). Workers consume their own queue plus the shared one; the APIs address a message to
`server.host` / `volume.host` (`compute.clients.worker_queue`, `block_store.clients.worker_host`). Per-node settings
(`COMPUTE_HOST`, `BLOCK_STORE_HOST`) live in a systemd drop-in, not in the shared `cloudx.env`. LVM volumes are
local to their node and reach other nodes over iSCSI (`block_store_worker/drivers/iscsi.py` exports,
`compute_worker/iscsi.py` logs in); see `two-node-lvm-lab.md`.

## Database schema

Only Alembic creates or alters tables; services refuse to start if the schema has not been migrated. See
`services/migrations/README.md` (commands: `sudo make migrate`, `cloudx-db-migrate --revision <svc> --autogenerate`).

## Tests

```
tests/
  conftest.py        required env, --live option, per-test os.environ isolation, `networking` and `cloudx` fixtures
  stack.py           fresh_service(): re-imports one service on top of a temporary sqlite database
  <service>/         unit tests per service (auth, compute, compute_worker, networking, block_store, image,
                     key_manager, k8s_engine, load_balancer, placement, metadata, cli, scripts, shared)
  live/              API tests against a running lab; only run with --live
```

- `pytest` (or `make test`): unit tests only, no lab needed, about a minute. This is what CI runs
  (`.github/workflows/tests.yml`, together with `make lint`). Dependencies: `pip install -r requirements-dev.txt`.
- `pytest --live tests/live` (or `make test-live`): API tests against the lab. Needs `AUTH_ADMIN_PASSWORD` and
  `AUTH_SECRET_KEY` (see `/etc/cloudx/cloudx.env` on the controller); service URLs can be overridden with
  `<SERVICE>_API_URL` (default `127.0.0.1`, for example through an SSH tunnel). At the end of the session,
  `tests/live/cleanup.py` removes the resources the tests created (routers, networks, keypairs, fake resource
  providers, and so on); it can also be run manually.
- Tests for the split services use `fresh_service(...)` / the `networking` fixture; the `shared/` tests cover the auth
  middleware, `shared.proc`, Alembic migrations, and the error-handling guard.
- Add new tests to the folder of the service they belong to. Tests that need a lab go in `tests/live` and must clean up
  after themselves (or use a name pattern that `cleanup.py` recognises).

## Error handling

- Do not swallow errors silently. An `except Exception:` must log (`log.warning(...)`), narrow the exception type, or,
  if it really is best-effort, state the reason on the `except` line (`# best-effort: <reason>`).
  `tests/test_error_handling.py` fails on new silent handlers that have no reason.
- Reconcilers (block_store_worker, networking, image_worker) act on what they observe. A failed existence check
  returns `None` (unknown) and the record is skipped; only `False` means "does not exist".
- RabbitMQ events and response enrichment (image names, floating IPs, and so on) are best-effort: failures are logged as
  warning/debug and do not fail the request.

## Running external commands

All services run commands (lvm, qemu-img, rbd, ovn-nbctl, virsh, mc, ...) through `shared.proc`, never through
`subprocess`/`os.system` directly (`tests/test_proc.py` fails on direct use):

- `run([...])` always takes a **list** (strings are rejected, there is no shell), has a **timeout** (default 60 seconds;
  `timeout=None` is allowed but must be explicit, for example when copying a whole image), and raises `CommandError`
  (a `RuntimeError` subclass) carrying the exit code and stderr. A missing binary and a timeout are reported the same
  way (exit 127 / 124).
- `succeeds([...])` is for probes (`lvdisplay`, `ovs-vsctl br-exists`, ...): `True`/`False`, and it raises
  `CommandError` if the probe itself cannot run. That outcome means "unknown", not "does not exist".
- `secrets=[...]` masks secret values in logs and error messages (error messages are also stored in API fields).
  MinIO credentials are passed through the environment (`MC_HOST_cloudx`), not as command arguments.
- Exceptions: the dashboard's interactive shell (`asyncio.create_subprocess_exec` with a PTY). The `cloudx` CLI is
  standalone (no `shared`), so it uses `subprocess.run` with an argument list and validates service names.
