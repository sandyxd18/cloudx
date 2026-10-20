"""Instance HA monitor: decide with shared.ha_logic, act through the compute and block-store APIs."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

import requests
from sqlalchemy import select

from shared.ha_logic import is_stale, next_state, partition

log = logging.getLogger("ha-api")
URLS = {"compute": os.getenv("COMPUTE_API_URL", "http://127.0.0.1:8003"),
        "block": os.getenv("BLOCK_STORE_API_URL", "http://127.0.0.1:8004")}
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
        r = requests.request(method, URLS[svc] + path, headers=h, json=body, timeout=10)
        if r.status_code >= 400:
            raise Failure(f"{method} {svc}{path} -> {r.status_code} {r.text[:150]}")
        return r.json() if r.content else {}

    def servers(self) -> list[dict]:
        return self.call("compute", "GET", "/internal/servers")["servers"]

    def server(self, sid: str) -> dict:
        return self.call("compute", "GET", f"/servers/{sid}")

    def server_volume_hosts(self, sid: str) -> list[str | None]:
        """Host of every volume attached to the server (root included); [] means unknown."""
        items = self.call("block", "GET", "/volumes")["items"]
        return [v.get("host") for v in items if v.get("attached_to_server_id") == sid]

    def force_down(self, host: str) -> None:
        self.call("compute", "PUT", f"/os-services/{host}", {"forced_down": True})

    def fence(self, iqn: str, volume_ids: list[str]) -> None:
        res = self.call("block", "POST", "/internal/fence", {"initiator_iqn": iqn, "volume_ids": volume_ids})["results"]
        bad = {k: v for k, v in res.items() if v != "ok"}
        if bad:
            raise Failure(f"fence failed: {bad}")

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
    """Order is fixed: forced_down, fence iSCSI, evacuate. A fence failure means no evacuate at all."""
    from .main import Protected
    prot = {p.server_id: p.project_id for p in db.scalars(select(Protected))}
    mine = [s for s in api.servers() if s["host"] == h.host and s["id"] in prot and s["status"] != "DELETED"]
    ok, skipped = partition(mine, set(prot), h.host, {s["id"]: api.server_volume_hosts(s["id"]) for s in mine})
    if ok and not h.initiator_iqn:
        skipped += [(sid, "host never reported an initiator iqn; cannot fence") for sid in ok]
        ok = []
    try:
        api.force_down(h.host)
        if ok:
            api.fence(h.initiator_iqn, [v for sid in ok for v in api.server_volume_ids(sid)])
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
        r = _rec(db, h.host, sid, prot[sid], "running", nowd or None)
        try:
            api.evacuate(sid)
            good = _wait_active(api, sid)
            r.status, r.message = ("done", nowd or None) if good else ("failed", f"not ACTIVE within {VERIFY}s")
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