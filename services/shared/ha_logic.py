"""Pure decisions for instance HA (no I/O): staleness, which servers may be evacuated, host state transitions."""
from datetime import datetime

MAX_FENCE_FAILS = 3
# What the volume listing of ha-api reports as the "host" of a Ceph volume: it is on no compute node, so it survives
# any of them, and no real host name can look like this.
SHARED_CEPH = "(ceph)"

def is_stale(last_seen: datetime, now: datetime, limit: int = 60) -> bool:
    return (now - last_seen).total_seconds() > limit

def partition(servers: list[dict], protected: set[str], dead_host: str,
              vol_hosts: dict[str, list[str | None]]) -> tuple[list[str], list[tuple[str, str]]]:
    """(evacuate ids, [(skipped id, reason)]). A server is safe only if every volume is on a known, living host.

    A MIGRATING server is never evacuated automatically: its guest may already run on the live-migration
    target, and a second copy on the same disks would corrupt them. An operator decides (docs/live-migration.md).
    """
    ok: list[str] = []
    skipped: list[tuple[str, str]] = []
    for s in servers:
        if s["host"] != dead_host or s["id"] not in protected:
            continue
        hosts = vol_hosts.get(s["id"]) or [None]  # no volume info = cannot prove safe
        if s.get("status") == "MIGRATING":
            skipped.append((s["id"], "live migration in progress; check the target before evacuating"))
        elif any(h is None for h in hosts):
            skipped.append((s["id"], "volume host unknown"))
        elif dead_host in hosts:
            skipped.append((s["id"], "volume on failed host"))
        else:
            ok.append(s["id"])
    return ok, skipped

def next_state(state: str, stale: bool, confirmed: bool, fails: int) -> str:
    if state == "up":
        return "suspect" if stale else "up"
    if state == "suspect":
        if not stale:
            return "up"
        return "recovering" if confirmed and fails < MAX_FENCE_FAILS else "suspect"
    return state  # fenced / recovering only change by the monitor or an operator reset
