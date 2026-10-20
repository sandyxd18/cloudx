"""Create and delete database instances by calling the other CloudX APIs with the caller's own token.

Each created resource id is saved on the row right away, so a crash or failure leaves a record that delete can clean up.
ponytail: one thread per request, no queue; a db-api restart marks unfinished rows as error (documented).
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time

import requests

from shared.db_cloudinit import PORTS, READY, render
from shared.db_validate import check_cidr

log = logging.getLogger("database-api")
URLS = {
    "net": os.getenv("NETWORKING_API_URL", "http://127.0.0.1:8001"),
    "compute": os.getenv("COMPUTE_API_URL", "http://127.0.0.1:8003"),
    "block": os.getenv("BLOCK_STORE_API_URL", "http://127.0.0.1:8004"),
    "file": os.getenv("FILE_STORE_API_URL", "http://127.0.0.1:8017"),
    "container": os.getenv("CONTAINER_API_URL", "http://127.0.0.1:8018"),
}
POLL = 5  # seconds between status polls (tests set 0)
TIMEOUT = {"vm": 900, "container": 300, "step": 300, "backup": 3600}
IMAGES = {"postgresql": "postgres:16", "mariadb": "mariadb:11"}
CONTAINER_ENV = {
    "postgresql": lambda pw: {"POSTGRES_PASSWORD": pw, "PGDATA": "/var/lib/postgresql/data/pgdata"},
    "mariadb": lambda pw: {"MARIADB_ROOT_PASSWORD": pw, "MARIADB_ROOT_HOST": "%"},
}
DATA_PATH = {"postgresql": "/var/lib/postgresql/data", "mariadb": "/var/lib/mysql"}
INIT_DONE = {"postgresql": "init process complete", "mariadb": "init process done"}  # the ready line only counts after this (the init server prints it too); logs are tail-capped, so no counting

class Failure(RuntimeError):
    pass

class Api:
    def __init__(self, headers: dict):
        self.headers = headers

    def call(self, svc, method, path, body=None, ok=(200, 201, 202, 204)):
        r = requests.request(method, URLS[svc] + path, headers=self.headers, json=body, timeout=60)
        if r.status_code == 404 and 404 in ok:
            return None
        if r.status_code not in ok:
            raise Failure(f"{method} {svc}{path} -> {r.status_code} {r.text[:150]}")
        return r.json() if r.content else {}

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def wait(fn, timeout: float, what: str):
    end = time.monotonic() + timeout
    while True:
        r = fn()
        if r:
            return r
        if time.monotonic() >= end:
            raise Failure(f"timed out waiting for {what}")
        time.sleep(POLL)

def _upd(sf, Inst, iid, **kw):
    with sf() as db:
        row = db.get(Inst, iid)
        if row:
            for k, v in kw.items():
                setattr(row, k, v)
            db.commit()

def _fail(sf, Inst, iid, exc):
    log.error("database %s: %s", iid, exc)
    _upd(sf, Inst, iid, status="error", error=str(exc)[:300])

def backup_watch(sf, Backup, bid: str, api) -> None:
    """Follow the block-store backup until it is available or failed."""
    with sf() as db:
        vb = db.get(Backup, bid).volume_backup_id
    try:
        d = wait(lambda: (lambda x: x if x["status"] in ("available", "error") else None)(api.call("block", "GET", f"/backups/{vb}")),
                 TIMEOUT["backup"], "the volume backup")
        _upd(sf, Backup, bid, status=d["status"], error=(d.get("error") or "backup failed") if d["status"] == "error" else None)
    except Exception as exc:  # best-effort: record on the row; the user can delete the backup and retry
        _upd(sf, Backup, bid, status="error", error=str(exc)[:300])

def build(sf, Inst, iid: str, api, s: dict) -> None:
    try:
        _build(sf, Inst, iid, api, s)
    except Exception as exc:  # best-effort: record the failure on the row; delete cleans the partial resources
        _fail(sf, Inst, iid, exc)

def _build(sf, Inst, iid, api, s):
    def up(**kw):
        _upd(sf, Inst, iid, **kw)
    name, engine, port = f"db-{iid[:8]}", s["engine"], PORTS[s["engine"]]
    cidr = s["allowed_cidr"]
    if not cidr:
        net = api.call("net", "GET", f"/networks/{s['network_id']}")
        cidr = api.call("net", "GET", f"/subnets/{net['subnets'][0]}")["cidr"]
    cidr = check_cidr(cidr)
    sg = api.call("net", "POST", "/security-groups", {"name": name, "description": "database instance"})
    up(sg_id=sg["id"], allowed_cidr=cidr)
    rules = f"/security-groups/{sg['id']}/rules"
    api.call("net", "POST", rules, {"direction": "ingress", "protocol": "tcp", "port_min": port, "port_max": port,
                                    "remote_ip_prefix": cidr})
    api.call("net", "POST", rules, {"direction": "egress", "protocol": "any", "remote_ip_prefix": "0.0.0.0/0"})
    if s["backend"] == "container":
        c = api.call("container", "POST", "/containers", {
            "name": name, "image": IMAGES[engine], "network_id": s["network_id"], "cpu": s["cpu"],
            "memory_mb": s["memory_mb"], "env": CONTAINER_ENV[engine](s["password"]), "data_path": DATA_PATH[engine],
            "security_group_ids": [sg["id"]]})
        up(container_id=c["id"])

        def container_up():
            d = api.call("container", "GET", f"/containers/{c['id']}")
            if d["status"] == "error":
                raise Failure(f"container failed: {d.get('error')}")
            if d["status"] != "running":
                return None
            logs = api.call("container", "GET", f"/containers/{c['id']}/logs")["logs"]
            i = logs.rfind(INIT_DONE[engine])
            return d if i >= 0 and READY[engine] in logs[i:] else None
        d = wait(container_up, TIMEOUT["container"], "the database container")
        up(ip=d["ip"], status="active")
        return
    storage, nfs, vol, share_id = s["storage"], None, None, None
    if storage == "block":
        if s.get("backup_volume_id"):  # restore: the data volume comes from the backup, not an empty one
            vol = api.call("block", "POST", f"/backups/{s['backup_volume_id']}/restore", {"name": name})
            up(volume_id=vol["id"])
            wait(lambda: api.call("block", "GET", f"/volumes/{vol['id']}")["status"] == "available", TIMEOUT["step"], "the restored volume")
        else:
            vol = api.call("block", "POST", "/volumes", {"name": name, "size_gb": s["size_gb"]})
            up(volume_id=vol["id"])
    elif storage == "file":
        sn = api.call("file", "POST", "/share-networks", {"name": name, "network_id": s["network_id"]})
        up(share_net_id=sn["id"])
        sn = wait(lambda: (lambda d: d if d["status"] == "active" else None)(api.call("file", "GET", f"/share-networks/{sn['id']}")),
                  TIMEOUT["step"], "the share network")
        sh = api.call("file", "POST", "/shares", {"name": name, "size_gb": s["size_gb"], "share_network_id": sn["id"]})
        share_id = sh["id"]
        up(share_id=share_id)
        wait(lambda: api.call("file", "GET", f"/shares/{share_id}")["status"] == "available", TIMEOUT["step"], "the share")
        nfs = f"{sn['ip']}:/{share_id}"
    token = secrets.token_urlsafe(24)
    up(token_hash=token_hash(token))
    user_data = render(engine, storage, s["password"], f"{s['public_url']}/internal/db-ready/{iid}", token, cidr=cidr, nfs=nfs, restore=bool(s.get("restore")))
    srv = api.call("compute", "POST", "/servers", {"name": name, "image": s["image"], "flavor": s["flavor"],
                                                   "network": s["network_id"], "security_groups": [sg["id"]],
                                                   "user_data": user_data})
    up(server_id=srv["id"])

    def server_active():
        d = api.call("compute", "GET", f"/servers/{srv['id']}")
        if d["status"] == "ERROR":
            raise Failure("server went to ERROR")
        return d if d["status"] == "ACTIVE" and d.get("ip_address") else None
    d = wait(server_active, TIMEOUT["step"], "the server")
    up(ip=d["ip_address"])
    if vol:
        api.call("block", "POST", f"/volumes/{vol['id']}/attach", {"server": srv["id"]})
    if share_id:
        r = api.call("file", "POST", f"/shares/{share_id}/access", {"to": d["ip_address"], "level": "rw"})
        up(rule_id=r["id"])

    def ready():
        with sf() as db:
            if db.get(Inst, iid).status == "active":
                return True
        if api.call("compute", "GET", f"/servers/{srv['id']}")["status"] == "ERROR":
            raise Failure("server went to ERROR")
        return False
    wait(ready, TIMEOUT["vm"], "the database to report ready")

def _gone(api, svc, path):
    def check():
        d = api.call(svc, "GET", path, ok=(200, 404))
        return d is None or d.get("status") == "DELETED"  # compute keeps a DELETED record
    return check

def teardown(sf, Inst, iid: str, api) -> None:
    try:
        _teardown(sf, Inst, iid, api)
    except Exception as exc:  # best-effort: keep the row (status error) so delete can be retried
        _fail(sf, Inst, iid, exc)

def _teardown(sf, Inst, iid, api):
    with sf() as db:
        row = db.get(Inst, iid)
        r = {k: getattr(row, k) for k in ("server_id", "container_id", "volume_id", "share_id", "share_net_id", "rule_id", "sg_id")}
    gone = (200, 202, 204, 404)
    if r["server_id"]:
        api.call("compute", "DELETE", f"/servers/{r['server_id']}", ok=gone)
        wait(_gone(api, "compute", f"/servers/{r['server_id']}"), TIMEOUT["step"], "the server to be deleted")
        _upd(sf, Inst, iid, server_id=None)
    if r["container_id"]:
        api.call("container", "DELETE", f"/containers/{r['container_id']}", ok=gone)
        wait(_gone(api, "container", f"/containers/{r['container_id']}"), TIMEOUT["step"], "the container to be deleted")
        _upd(sf, Inst, iid, container_id=None)
    if r["volume_id"]:
        api.call("block", "POST", f"/volumes/{r['volume_id']}/detach", ok=gone + (409,))
        api.call("block", "DELETE", f"/volumes/{r['volume_id']}", ok=gone)
        _upd(sf, Inst, iid, volume_id=None)
    if r["rule_id"] and r["share_id"]:
        api.call("file", "DELETE", f"/shares/{r['share_id']}/access/{r['rule_id']}", ok=gone)
        _upd(sf, Inst, iid, rule_id=None)
    if r["share_id"]:
        api.call("file", "DELETE", f"/shares/{r['share_id']}", ok=gone)
        wait(_gone(api, "file", f"/shares/{r['share_id']}"), TIMEOUT["step"], "the share to be deleted")
        _upd(sf, Inst, iid, share_id=None)
    if r["share_net_id"]:
        api.call("file", "DELETE", f"/share-networks/{r['share_net_id']}", ok=gone)
        wait(_gone(api, "file", f"/share-networks/{r['share_net_id']}"), TIMEOUT["step"], "the share network to be deleted")
        _upd(sf, Inst, iid, share_net_id=None)
    if r["sg_id"]:
        def del_sg():  # the port may still be going away
            try:
                api.call("net", "DELETE", f"/security-groups/{r['sg_id']}", ok=gone)
                return True
            except Failure:
                return False
        wait(del_sg, TIMEOUT["step"], "the security group to be deletable")
        _upd(sf, Inst, iid, sg_id=None)
    with sf() as db:
        db.delete(db.get(Inst, iid))
        db.commit()
