#!/usr/bin/env python3
"""
cloudx — Unified CLI for cloudx cloud platform
================================================
Usage: cloudx [OPTIONS] COMMAND [ARGS]...

Authentication:
  Reads ~/.cloudx/credentials (INI format) for access key authentication.
  Configure with: cloudx auth configure
  Or set env vars: CLOUDX_ACCESS_KEY_ID + CLOUDX_SECRET_ACCESS_KEY

Examples:
  cloudx auth configure
  cloudx server list
  cloudx network create my-net
  cloudx volume create my-vol --size 20
"""
from cloudx_cli.app import cli
# Importing a commands module registers its groups on `cli`.
from cloudx_cli.commands import (  # noqa: F401
    alarm,
    auth,
    compute,
    image,
    key_manager,
    kubernetes,
    load_balancer,
    network,
    object_store,
    observability,
    orchestration,
    role_assignment,
    service,
    stack,
    storage,
)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cli()
