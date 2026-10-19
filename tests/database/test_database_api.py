import importlib

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth


class Fake:
    """Stands in for the other CloudX APIs; records what exists so cleanup can be asserted."""

    def __init__(self, fail_on=None):
        self.alive: set[str] = set()
        self.calls: list[tuple] = []
        self.fail_on = fail_on
        self.n = 0
        self.server_status = "ACTIVE"
        self.on_server = None  # called when a server is created (the VM "calls back")

    def _new(self, kind):
        self.n += 1
        i = f"{kind}{self.n}"
        self.alive.add(i)
        return i

    def call(self, svc, method, path, body=None, ok=(200, 201, 202, 204)):
        from services.database.app import provision
        self.calls.append((svc, method, path, body))
        key = f"{method} {path.split('?')[0]}"
        if self.fail_on and key.startswith(self.fail_on):
            raise provision.Failure(f"boom {key}")
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/security-groups":
            return {"id": self._new("sg")}
        if method == "GET" and parts[0] == "networks":
            return {"subnets": ["sub1"]}
        if method == "GET" and parts[0] == "subnets":
            return {"cidr": "10.0.0.0/24"}
        if method == "POST" and path == "/volumes":
            return {"id": self._new("vol")}
        if method == "POST" and path == "/share-networks":
            return {"id": self._new("sn")}
        if method == "POST" and path == "/shares":
            return {"id": self._new("sh")}
        if method == "POST" and path == "/containers":
            return {"id": self._new("ct")}
        if method == "POST" and path == "/servers":
            sid = self._new("srv")
            if self.on_server:
                self.on_server(sid)
            return {"id": sid}
        if method == "POST" and path.endswith("/access"):
            return {"id": self._new("rule")}
        if method == "GET":
            if parts[0] in ("servers", "containers", "shares", "share-networks") and parts[1] not in self.alive:
                return {"status": "DELETED"} if parts[0] == "servers" else None
            if parts[0] == "servers":
                return {"status": self.server_status, "ip_address": "10.0.0.50"}
            if parts[0] == "containers" and len(parts) == 3:
                return {"logs": "init process complete\nready to accept connections\ninit process done\nready for connections\n"}
            if parts[0] == "containers":
                return {"status": "running", "ip": "10.0.0.60"}
            if parts[0] == "shares":
                return {"status": "available"}
            if parts[0] == "share-networks":
                return {"id": parts[1], "status": "active", "ip": "10.0.0.9"}
        if method == "DELETE":
            self.alive.discard(parts[1] if parts[0] != "shares" or len(parts) == 2 else parts[3])
            return {}
        return {}


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'db.sqlite'}")
    import services.database.app.main as m
    m = importlib.reload(m)
    m.provision.POLL = 0
    m.provision.TIMEOUT = {"vm": 1, "container": 1, "step": 1}
    m.Base.metadata.create_all(bind=m.engine)
    fake = Fake()
    monkeypatch.setattr(m, "run_async", lambda fn, *a: fn(*a))
    monkeypatch.setattr(m, "make_api", lambda headers: fake)
    who = {"project_id": "p1", "role": "member"}
    m.app.dependency_overrides[require_auth] = lambda: who
    c = TestClient(m.app, headers={"X-Auth-Token": "t"})
    c.who, c.fake, c.m = who, fake, m
    fake.on_server = lambda sid: None
    return c


VM = {"name": "d1", "engine": "postgresql", "backend": "vm", "network_id": "n1", "image": "ubuntu", "flavor": "small"}


def ready_cb(c, iid, token):
    return c.post(f"/internal/db-ready/{iid}", headers={"X-Ready-Token": token})


def vm_ready(c, body):
    """Create a VM instance; the 'VM' calls back as soon as the server exists. Returns (id, response)."""
    seen = {}

    def cb(_sid):
        import re
        ud = [x[3]["user_data"] for x in c.fake.calls if x[2] == "/servers"][-1]
        tok = re.search(r"X-Ready-Token: (\S+)'", ud).group(1)
        iid = re.search(r"db-ready/([^']+)'", ud).group(1)
        seen["r"] = ready_cb(c, iid, tok)
    c.fake.on_server = cb
    r = c.post("/instances", json=body)
    return r, seen.get("r")


def test_vm_local(db):
    r, cb = vm_ready(db, VM)
    assert r.status_code == 201 and r.json()["password"] and cb.status_code == 200
    i = db.get(f"/instances/{r.json()['id']}").json()
    assert i["status"] == "active" and i["host"] == "10.0.0.50" and i["port"] == 5432 and "password" not in i
    sg_rule = next(x for x in db.fake.calls if x[2].endswith("/rules"))
    assert sg_rule[3]["port_min"] == 5432 and sg_rule[3]["remote_ip_prefix"] == "10.0.0.0/24"
    assert not any(x[0] == "block" or x[0] == "file" for x in db.fake.calls)


def test_vm_block_and_file(db):
    r, _ = vm_ready(db, {**VM, "name": "b", "engine": "mariadb", "storage": "block", "size_gb": 5})
    assert db.get(f"/instances/{r.json()['id']}").json()["status"] == "active"
    assert ("block", "POST", "/volumes/vol2/attach", {"server": "srv3"}) in db.fake.calls
    r, _ = vm_ready(db, {**VM, "name": "f", "engine": "mariadb", "storage": "file", "size_gb": 5})
    assert db.get(f"/instances/{r.json()['id']}").json()["status"] == "active"
    acc = next(x for x in db.fake.calls if x[2].endswith("/access"))
    assert acc[3] == {"to": "10.0.0.50", "level": "rw"}
    ud = [x[3]["user_data"] for x in db.fake.calls if x[2] == "/servers"][-1]
    assert "10.0.0.9:/sh" in ud


def test_container(db):
    r = db.post("/instances", json={"name": "c", "engine": "postgresql", "backend": "container", "network_id": "n1"})
    assert r.status_code == 201
    i = db.get(f"/instances/{r.json()['id']}").json()
    assert i["status"] == "active" and i["host"] == "10.0.0.60"
    c = next(x[3] for x in db.fake.calls if x[2] == "/containers")
    assert c["data_path"] == "/var/lib/postgresql/data" and c["image"] == "postgres:16"


@pytest.mark.parametrize("extra", [
    {"backend": "container", "storage": "block", "size_gb": 5}, {"backend": "container", "storage": "file", "size_gb": 5},
    {"storage": "block"}, {"storage": "file", "size_gb": 5}, {"engine": "mysql"}, {"password": "short"}, {"allowed_cidr": "0.0.0.0/0"},
    {"image": None}, {"storage": "nope"},
])
def test_rejected(db, extra):
    assert db.post("/instances", json={**VM, **extra}).status_code == 422


def test_duplicate_and_isolation(db):
    vm_ready(db, VM)
    assert db.post("/instances", json=VM).status_code == 409
    iid = db.get("/instances").json()["items"][0]["id"]
    db.who.update(project_id="p2")
    assert db.get(f"/instances/{iid}").status_code == 404
    assert db.get("/instances").json()["items"] == []


def test_callback_single_use_and_wrong_token(db):
    seen = {}

    def cb(_sid):
        import re
        ud = [x[3]["user_data"] for x in db.fake.calls if x[2] == "/servers"][-1]
        seen["tok"] = re.search(r"X-Ready-Token: (\S+)'", ud).group(1)
        seen["id"] = re.search(r"db-ready/([^']+)'", ud).group(1)
        seen["bad"] = ready_cb(db, seen["id"], "wrong").status_code
        seen["ok"] = ready_cb(db, seen["id"], seen["tok"]).status_code
        seen["again"] = ready_cb(db, seen["id"], seen["tok"]).status_code
    db.fake.on_server = cb
    db.post("/instances", json=VM)
    assert (seen["bad"], seen["ok"], seen["again"]) == (403, 200, 403)
    assert db.post("/internal/db-ready/nope").status_code == 403


STEPS = ["POST /security-groups", "POST /volumes", "POST /servers", "POST /volumes/vol2/attach"]


@pytest.mark.parametrize("step", STEPS)
def test_failure_keeps_record_and_delete_cleans(db, step):
    db.fake.fail_on = step
    r = db.post("/instances", json={**VM, "storage": "block", "size_gb": 3})
    iid = r.json()["id"]
    i = db.get(f"/instances/{iid}").json()
    assert i["status"] == "error" and "boom" in i["error"]
    db.fake.fail_on = None
    assert db.delete(f"/instances/{iid}").status_code == 202
    assert db.get(f"/instances/{iid}").status_code == 404
    assert db.fake.alive == set()


def test_failure_on_file_chain_cleans(db):
    db.fake.fail_on = "POST /shares/sh"  # the access rule
    r = db.post("/instances", json={**VM, "engine": "mariadb", "storage": "file", "size_gb": 3})
    iid = r.json()["id"]
    assert db.get(f"/instances/{iid}").json()["status"] == "error"
    db.fake.fail_on = None
    db.delete(f"/instances/{iid}")
    assert db.get(f"/instances/{iid}").status_code == 404 and db.fake.alive == set()


def test_delete_container_and_power(db):
    r = db.post("/instances", json={"name": "c", "engine": "mariadb", "backend": "container", "network_id": "n1"})
    iid = r.json()["id"]
    assert db.post(f"/instances/{iid}/start").status_code == 409
    assert db.post(f"/instances/{iid}/stop").json()["status"] == "stopped"
    assert ("container", "POST", "/containers/ct2/stop", None) in db.fake.calls
    assert db.post(f"/instances/{iid}/start").json()["status"] == "active"
    db.delete(f"/instances/{iid}")
    assert db.get("/instances").json()["items"] == [] and db.fake.alive == set()


def test_restart_marks_stale_rows(db):
    m = db.m
    with m.SessionLocal() as s:
        s.add(m.Instance(id="x", name="x", project_id="p1", engine="postgresql", backend="vm", storage="local",
                         network_id="n", status="building"))
        s.commit()
    with m.engine.begin() as cx:
        cx.exec_driver_sql("create table alembic_version_database (version_num varchar)")
    with TestClient(m.app):
        pass
    assert db.get("/instances/x").json()["status"] == "error"
