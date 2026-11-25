"""Commands that empty a compute node (`host drain`) and put it back in service (`host enable`).

They extend the `host` group that compute.py defines."""
import time

import click

from cloudx_cli.commands.compute import host
from cloudx_cli.common import DEFAULT_URLS, _api, _output

POLL_SECONDS = 5
SERVER_PAGE = 1000  # the API's largest `limit`


def _all_servers(compute: str, profile: str) -> list[dict]:
    """Every server the caller can see, page by page: `limit` applies to the whole cloud, so one page could miss
    servers on the node. GET /servers pages with `marker` (the id of the last server of the previous page); a page
    shorter than the limit is the last one. GET /volumes has no paging and is read in one call."""
    servers, marker = [], None
    while True:
        url = f"{compute}/servers?limit={SERVER_PAGE}" + (f"&marker={marker}" if marker else "")
        page = _api("get", url, profile).get("items", [])
        servers += page
        if len(page) < SERVER_PAGE:
            return servers
        marker = page[-1]["id"]


def plan_drain(node: str, servers: list[dict], volumes: list[dict]) -> list[dict]:
    """What a drain does with each server on `node`: {"server", "id", "action", "reason"}, where action is
    live-migrate, cold-migrate or skip. A server is held back (skip) when it is not ACTIVE or SHUTOFF, or when it is
    ACTIVE and one of its volumes (root or attached) is a local LV on `node`: that disk cannot follow it live."""
    plan = []
    for s in servers:
        if s.get("host") != node:
            continue
        status = s.get("status")
        entry = {"server": s.get("name") or s["id"], "id": s["id"]}
        if status == "SHUTOFF":
            plan.append({**entry, "action": "cold-migrate", "reason": "stopped: moved cold"})
            continue
        if status != "ACTIVE":
            plan.append({**entry, "action": "skip", "reason": f"status is {status}, only ACTIVE and SHUTOFF servers are moved"})
            continue
        local = [v for v in volumes
                 if (v.get("attached_to_server_id") == s["id"] or v.get("id") == s.get("root_volume_id"))
                 and v.get("host") == node and v.get("backend") != "ceph"]
        if local:
            names = ", ".join(v.get("name") or v["id"] for v in local)
            plan.append({**entry, "action": "skip", "reason": f"volume {names} is a local LV on {node}"})
        else:
            plan.append({**entry, "action": "live-migrate", "reason": "running, no local volume on the node"})
    return plan


def _say(ctx, message: str) -> None:
    # keep stdout parseable when the output is json
    click.echo(message, err=ctx.obj["fmt"] == "json")


def _wait_for_move(ctx, node: str, entry: dict, timeout: int) -> tuple[bool, str]:
    """Poll the server until the migration is over. Returns (moved, reason)."""
    want = "SHUTOFF" if entry["action"] == "cold-migrate" else "ACTIVE"
    url = f"{DEFAULT_URLS['compute']}/servers/{entry['id']}"
    started, waited = time.monotonic(), 0
    while True:
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        try:
            current = _api("get", url, ctx.obj["profile"])
        except SystemExit:
            return False, "could not read the server status"
        status, where = current.get("status"), current.get("host")
        if status == "ERROR":
            return False, f"status is ERROR (host {where})"
        if status == want:
            if where and where != node:
                return True, f"moved to {where}"
            return False, f"came back {status} on {node}: the migration was cancelled or rolled back"
        if max(waited, time.monotonic() - started) >= timeout:
            return False, f"timed out after {timeout}s, status {status}"


@host.command("enable")
@click.argument("node")
@click.pass_context
def host_enable(ctx, node):
    """Let the scheduler place servers on NODE again (after `host drain`)."""
    _api("put", f"{DEFAULT_URLS['compute']}/os-services/{node}", ctx.obj["profile"], json={"disabled": False})
    click.echo(f"✓ Host {node} is enabled")


@host.command("drain")
@click.argument("node")
@click.option("--dry-run", is_flag=True, help="Only print the plan: nothing is disabled or migrated")
@click.option("--timeout", type=int, default=2400, show_default=True,
              help="Seconds to wait for each migration before it counts as failed")
@click.pass_context
def host_drain(ctx, node, dry_run, timeout):
    """Disable NODE and move its servers away, one at a time.

    Running servers are live-migrated and stopped ones cold-migrated. A server that cannot move (not ACTIVE or
    SHUTOFF, or a local LV on NODE) is skipped. The command exits non-zero when anything was skipped or failed.
    The node stays disabled either way; `cloudx host enable NODE` puts it back in service. Running it again only
    handles the servers still on NODE."""
    compute, profile = DEFAULT_URLS["compute"], ctx.obj["profile"]
    if not dry_run:
        _api("put", f"{compute}/os-services/{node}", profile, json={"disabled": True, "disabled_reason": "drain"})
    servers = _all_servers(compute, profile)
    volumes = _api("get", f"{DEFAULT_URLS['volume']}/volumes", profile).get("items", [])
    plan = plan_drain(node, servers, volumes)
    enable_hint = f"{node} stays disabled. Run `cloudx host enable {node}` to put it back in service."

    if not plan:
        _say(ctx, f"no servers on {node}" + ("" if dry_run else f". {enable_hint}"))
        ctx.exit(0)

    if dry_run:
        _output(plan, ctx.obj["fmt"], ["server", "action", "reason"])
        skipped = sum(p["action"] == "skip" for p in plan)
        _say(ctx, f"dry run: nothing was changed. " +
             (f"{skipped} server(s) would be skipped, so a real drain would exit non-zero." if skipped else "Every server can move."))
        ctx.exit(1 if skipped else 0)

    rows = []
    for entry in plan:
        if entry["action"] == "skip":
            rows.append({"server": entry["server"], "result": "skipped", "reason": entry["reason"]})
            continue
        path = "live-migrate" if entry["action"] == "live-migrate" else "migrate"
        try:
            _api("post", f"{compute}/servers/{entry['id']}/{path}", profile)
        except SystemExit:  # _api printed the refusal; go on with the next server
            rows.append({"server": entry["server"], "result": "failed", "reason": "the API refused the request (see the message above)"})
            continue
        click.echo(f"{entry['server']}: {entry['action']} started", err=True)
        moved, reason = _wait_for_move(ctx, node, entry, timeout)
        rows.append({"server": entry["server"], "result": "migrated" if moved else "failed", "reason": reason})

    _output(rows, ctx.obj["fmt"], ["server", "result", "reason"])
    bad = [r for r in rows if r["result"] != "migrated"]
    if bad:
        _say(ctx, f"{len(bad)} of {len(rows)} server(s) were skipped or failed and are still on {node}. {enable_hint}")
        ctx.exit(1)
    _say(ctx, f"All {len(rows)} server(s) moved off {node}. {enable_hint}")
    ctx.exit(0)
