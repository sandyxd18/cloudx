"""Instance HA monitor: decide with shared.ha_logic, act through the compute and block-store APIs."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

import requests
from sqlalchemy import select

from shared import catalog
from shared.ha_logic import SHARED_CEPH, is_stale, next_state, partition

log = logging.getLogger("ha-api")
SERVICES = {"compute": "compute", "block": "volume"}
POLL = 10     # seconds between cycles
CONFIRM = 30  # a suspect host must still be silent this long after it was first flagged
VERIFY = 300  # seconds an evacuated server gets to turn ACTIVE
WAIT = 5


class Failure(RuntimeError):
    pass


class Api:
    """Internal-token client. Tests replace this with a fake."""

    def call(self, svc, method, path, body=None):
        h = {"X-Internal-Token": os.getenv("INTERNAL_TOKEN", "")}
        r = requests.request(method, catalog.url(SERVICES[svc]) + path, headers=h, json=body, timeout=10)
        if r.status_code >= 400:
            raise Failure(f"{method} {svc}{path} -> {r.status_code} {r.text[:150]}")
        return r.json() if r.content else {}

    def servers(self) -> list[dict]:
        return self.call("compute", "GET", "/internal/servers")["servers"]

    def server(self, sid: str) -> dict:
        return self.call("compute", "GET", f"/servers/{sid}")

    def server_volume_hosts(self, sid: str) -> list[str | None]:
        """Host of every volume attached to the server (root included); [] means unknown. A Ceph volume has no host:
        it is reported as SHARED_CEPH, an LVM volume without a host as None."""
        items = self.call("block", "GET", "/volumes")["items"]
        return [v.get("host") or (SHARED_CEPH if (v.get("backend") or "").lower() == "ceph" else None)
                for v in items if v.get("attached_to_server_id") == sid]

    def force_down(self, host: str) -> None:
        self.call("compute", "PUT", f"/os-services/{host}", {"forced_down": True})

    def fence(self, iqn: str, volume_ids: list[str]) -> list[str]:
        """Cut the dead node off the volumes. Raises when any of them could not be fenced; returns the ids of the Ceph
        volumes on which no client was found (nothing to cut off: a powered-off node, or a watch that has expired)."""
        res = self.call("block", "POST", "/internal/fence", {"initiator_iqn": iqn, "volume_ids": volume_ids})["results"]
        bad = {k: v for k, v in res.items() if v not in ("ok", "no-watcher")}
        if bad:
            raise Failure(f"fence failed: {bad}")
        return [k for k, v in res.items() if v == "no-watcher"]

    def evacuate(self, sid: str) -> None:
        self.call("compute", "POST", f"/servers/{sid}/evacuate")

    def server_status(self, sid: str) -> str:
        return self.server(sid).get("status", "")

    def server_volume_ids(self, sid: str) -> list[str]:
        items = self.call("block", "GET", "/volumes")["items"]
        return [v["id"] for v in items if v.get("attached_to_server_id") == sid]


def _rec(db, host, sid, project, status, msg=None):
    from .main import Recovery
    r = Recovery(host=host, server_id=sid, project_id=project, status=status, message=msg,
                 finished_at=datetime.utcnow() if status in ("done", "failed", "skipped") else None)
    db.add(r)
    db.commit()
    return r


def _wait_active(api, sid) -> bool:
    end = time.monotonic() + VERIFY
    while True:
        if api.server_status(sid) == "ACTIVE":
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(WAIT)


def recover(db, api, h) -> None:
    """Order is fixed: forced_down, fence (iSCSI ACL, Ceph blocklist), evacuate. A fence failure means no evacuate at all."""
    from .main import Protected
    prot = {p.server_id: p.project_id for p in db.scalars(select(Protected))}
    mine = [s for s in api.servers() if s["host"] == h.host and s["id"] in prot and s["status"] != "DELETED"]
    vol_hosts = {s["id"]: api.server_volume_hosts(s["id"]) for s in mine}
    ok, skipped = partition(mine, set(prot), h.host, vol_hosts)
    if not h.initiator_iqn:  # only an iSCSI volume needs the node's initiator name; a Ceph one is fenced without it
        needs = [sid for sid in ok if any(x != SHARED_CEPH for x in vol_hosts[sid])]
        skipped += [(sid, "host never reported an initiator iqn; cannot fence") for sid in needs]
        ok = [sid for sid in ok if sid not in needs]
    volumes = {sid: api.server_volume_ids(sid) for sid in ok}
    unfenced: list[str] = []
    try:
        api.force_down(h.host)
        if ok:
            unfenced = api.fence(h.initiator_iqn or "", [v for sid in ok for v in volumes[sid]])
    except Exception as exc:  # best-effort: any failure here must leave the host suspect and block evacuate
        h.fails += 1
        _rec(db, h.host, None, None, "failed", f"fencing attempt {h.fails} failed: {exc}")
        h.state = "suspect"
        db.commit()
        return
    for sid, why in skipped:
        _rec(db, h.host, sid, prot[sid], "skipped", why)
    nowd = "" if h.watchdog_at and (datetime.utcnow() - h.watchdog_at).total_seconds() <= 120 else \
        "no watchdog seen; old VMs may still run"
    for sid in ok:
        idle = [v for v in volumes[sid] if v in unfenced]
        note = "; ".join(filter(None, [nowd, f"no Ceph client found on {', '.join(idle)}; only the watchdog stops an old guest" if idle else ""]))
        r = _rec(db, h.host, sid, prot[sid], "running", note or None)
        try:
            api.evacuate(sid)
            good = _wait_active(api, sid)
            r.status, r.message = ("done", note or None) if good else ("failed", f"not ACTIVE within {VERIFY}s")
        except Exception as exc:  # best-effort: record and carry on with the next server
            r.status, r.message = "failed", str(exc)[:200]
        r.finished_at = datetime.utcnow()
        db.commit()
    h.state = "fenced"
    db.commit()


def cycle(db, api, now: datetime) -> None:
    from .main import Host
    for h in db.scalars(select(Host)).all():
        if h.state in ("fenced", "recovering"):
            continue
        stale = is_stale(h.last_seen, now)
        if h.state == "suspect" and not h.suspect_at:
            h.suspect_at = now
        confirmed = bool(h.suspect_at) and (now - h.suspect_at).total_seconds() >= CONFIRM
        new = next_state(h.state, stale, confirmed, h.fails)
        if new == "suspect" and h.state == "up":
            h.suspect_at = now
        if new == "up":
            h.suspect_at = None
        h.state = new
        db.commit()
        if new == "recovering":
            recover(db, api, h)