"""POST /servers/{ref}/live-migrate: compute-api checks the server, moves the placement claim to the target, sets
MIGRATING and casts `live_migrate` to the SOURCE worker, which drives the migration and reports through
/internal/servers/{id}/actions/finish. The server keeps its old host until the worker moves it."""
import importlib

import pytest
from shared.auth_middleware import require_auth

from tests.compute import test_catalog_failures
from tests.compute.test_catalog_failures import PEERS

api, _status = test_catalog_failures.api, test_catalog_failures._status

ROOT = {"id": "vol1", "attached_to_server_id": "srv1", "status": "in-use", "host": "store-1",
        "path": "rbd:volumes/vol1", "backend": "ceph", "target_device": None}
DATA = {"id": "vol-data", "attached_to_server_id": "srv1", "status": "in-use", "host": "store-1",
        "path": "/dev/cinder-volumes/vol-data", "backend": "lvm", "target_device": "/dev/sdb"}
OTHER = {"id": "vol-other", "attached_to_server_id": "someone-else", "status": "in-use", "host": "store-1",
         "path": "/dev/x", "backend": "lvm", "target_device": "sdb"}


def _server(svc, status="ACTIVE", **extra):
    fields = {"id": "srv1", "name": "vm1", "project_id": "p1", "image_id": "img1", "root_volume_id": "vol1",
              "flavor": "small", "network_id": "net1", "port_id": "port1", "host": "node-1", "status": status,
              "libvirt_domain_name": "cloudx-srv1", "instance_dir": "/var/lib/cloudx/instances/srv1"}
    fields.update(extra)
    db = svc.SessionLocal()
    db.add(svc.Server(**fields))
    db.commit()
    db.close()


def _host(svc):
    with svc.SessionLocal() as db:
        return db.get(svc.Server, "srv1").host


def _service(svc, host, **flags):
    with svc.SessionLocal() as db:
        db.add(svc.ComputeService(host=host, disabled=flags.get("disabled", False),
                                  forced_down=flags.get("forced_down", False)))
        db.commit()


@pytest.fixture
def wired(api, monkeypatch):
    svc, client, _, _ = api
    for key in PEERS:
        monkeypatch.setenv(key, "http://peer.test:1")
    state = {"volumes": [dict(ROOT), dict(DATA), dict(OTHER)], "claims": [], "restores": [], "casts": [],
             "claim_result": "node-2", "cast_error": None}

    def block_get(path):
        if path == "/volumes":
            return {"items": state["volumes"]}
        return next(v for v in state["volumes"] if path == f"/volumes/{v['id']}")

    def cast(method, payload):
        if state["cast_error"]:
            raise state["cast_error"]
        state["casts"].append((method, payload))

    def claim(db, server_id, project_id, flavor, **kw):
        state["claims"].append({"server_id": server_id, **kw})
        return state["claim_result"]

    ops = importlib.import_module("services.compute.app.routers.ops")
    monkeypatch.setattr(svc.clients, "cast_to_worker", cast)
    monkeypatch.setattr(svc.clients, "block_get", block_get)
    monkeypatch.setattr(svc.clients, "api_get", lambda url: {"id": url.rsplit("/", 1)[-1], "mac_address": "00:11:22:33:44:55"})
    monkeypatch.setattr(ops, "claim_host", claim)
    monkeypatch.setattr(ops, "fallback_host", lambda *a, **k: None)
    monkeypatch.setattr(ops, "compute_hosts", lambda: ["node-1", "node-2", "node-3"])
    monkeypatch.setattr(ops, "restore_source_claim", lambda db, server: state["restores"].append(server.host))
    return svc, client, state, ops


def test_a_live_migrate_casts_to_the_source_worker_and_marks_the_server_migrating(wired):
    svc, client, state, _ = wired
    _server(svc)

    r = client.post("/servers/srv1/live-migrate", json={"host": "node-2"})

    assert r.status_code == 202, r.text
    assert r.json() == {"server_id": "srv1", "from": "node-1", "to": "node-2", "status": "MIGRATING"}
    (method, payload), = state["casts"]
    assert method == "live_migrate"
    assert payload["host"] == "node-1", "cast to the source worker's queue"
    assert payload["id"] == "srv1"
    assert payload["_target_host"] == "node-2" and payload["_source_host"] == "node-1"
    assert payload["libvirt_domain_name"] == "cloudx-srv1"
    assert payload["instance_dir"] == "/var/lib/cloudx/instances/srv1"
    assert payload["port"]["id"] == "port1" and [p["id"] for p in payload["ports"]] == ["port1"]
    assert payload["_volumes"] == [
        {"volume_id": "vol1", "host": "store-1", "path": "rbd:volumes/vol1", "backend": "ceph", "target_dev": "sda"},
        {"volume_id": "vol-data", "host": "store-1", "path": "/dev/cinder-volumes/vol-data", "backend": "lvm",
         "target_dev": "sdb"},
    ]
    assert state["claims"] == [{"server_id": "srv1", "host": "node-2", "avoid": ("node-1",)}]
    assert _status(svc) == "MIGRATING"
    assert _host(svc) == "node-1", "the worker moves the host once the domain runs on the target"
    actions = client.get("/servers/srv1/os-instance-actions").json()["instanceActions"]
    assert [a["action"] for a in actions] == ["live_migrate"]


def test_without_a_host_the_scheduler_picks_one_away_from_the_source(wired):
    svc, client, state, _ = wired
    _server(svc)
    state["claim_result"] = "node-3"

    r = client.post("/servers/srv1/live-migrate")

    assert r.status_code == 202, r.text
    assert r.json()["to"] == "node-3"
    assert state["claims"] == [{"server_id": "srv1", "host": None, "avoid": ("node-1",)}]
    assert state["casts"][0][1]["_target_host"] == "node-3"


def _locked(svc, state):
    _server(svc, locked=True)


def _no_root(svc, state):
    _server(svc, root_volume_id=None)


def _available(svc, state):
    _server(svc)
    state["volumes"][1]["status"] = "available"


def _empty_path(svc, state):
    _server(svc)
    state["volumes"][1]["path"] = ""


def _disabled(svc, state):
    _server(svc)
    _service(svc, "node-2", disabled=True)


def _forced_down(svc, state):
    _server(svc)
    _service(svc, "node-2", forced_down=True)


REFUSALS = [
    ("shutoff", lambda svc, state: _server(svc, "SHUTOFF"), {"host": "node-2"}, "SHUTOFF", "status SHUTOFF"),
    ("migrating", lambda svc, state: _server(svc, "MIGRATING"), {"host": "node-2"}, "MIGRATING", "status MIGRATING"),
    ("locked", _locked, {"host": "node-2"}, "ACTIVE", "locked"),
    ("no root volume", _no_root, {"host": "node-2"}, "ACTIVE", "root volume"),
    ("volume not in-use", _available, {"host": "node-2"}, "ACTIVE", "vol-data"),
    ("volume without path", _empty_path, {"host": "node-2"}, "ACTIVE", "vol-data"),
    ("target disabled", _disabled, {"host": "node-2"}, "ACTIVE", "disabled"),
    ("target forced down", _forced_down, {"host": "node-2"}, "ACTIVE", "forced down"),
    ("target is the source", lambda svc, state: _server(svc), {"host": "node-1"}, "ACTIVE", "already on node-1"),
    ("unknown target", lambda svc, state: _server(svc), {"host": "node-9"}, "ACTIVE", "node-9"),
]


@pytest.mark.parametrize("setup,body,status,reason", [r[1:] for r in REFUSALS], ids=[r[0] for r in REFUSALS])
def test_each_refusal_is_a_409_with_its_reason(wired, setup, body, status, reason):
    svc, client, state, _ = wired
    setup(svc, state)

    r = client.post("/servers/srv1/live-migrate", json=body)

    assert r.status_code == 409, r.text
    assert reason in str(r.json()["detail"])
    assert state["casts"] == []
    assert state["claims"] == []
    assert _status(svc) == status
    assert _host(svc) == "node-1"


def test_a_second_call_while_migrating_is_a_409_and_makes_no_second_claim(wired):
    svc, client, state, _ = wired
    _server(svc)

    first = client.post("/servers/srv1/live-migrate", json={"host": "node-2"})
    second = client.post("/servers/srv1/live-migrate", json={"host": "node-3"})

    assert first.status_code == 202
    assert second.status_code == 409 and "MIGRATING" in second.json()["detail"]
    assert len(state["claims"]) == 1
    assert len(state["casts"]) == 1
    assert _status(svc) == "MIGRATING"


def test_a_member_is_refused_by_the_policy(wired):
    svc, client, state, _ = wired
    _server(svc)
    svc.app.dependency_overrides[require_auth] = lambda: {"role": "member", "project_id": "p1", "sub": "u"}

    r = client.post("/servers/srv1/live-migrate", json={"host": "node-2"})

    assert r.status_code == 403
    assert state["casts"] == [] and state["claims"] == []
    assert _status(svc) == "ACTIVE"


def test_another_projects_server_is_a_404(wired, pfile):
    """A system admin sees every project; a member let through by a policy file sees only its own."""
    svc, client, state, _ = wired
    _server(svc, project_id="p2")
    pfile.write('"compute:server:live_migrate": "role:member or role:admin"\n')
    svc.app.dependency_overrides[require_auth] = lambda: {"role": "member", "project_id": "p1", "sub": "u"}

    r = client.post("/servers/srv1/live-migrate", json={"host": "node-2"})

    assert r.status_code == 404, r.text
    assert state["casts"] == [] and state["claims"] == []


def test_no_candidate_host_is_a_409_no_valid_host(wired):
    svc, client, state, _ = wired
    _server(svc)
    state["claim_result"] = None

    r = client.post("/servers/srv1/live-migrate")

    assert r.status_code == 409
    assert "No valid host was found" in str(r.json()["detail"])
    assert state["casts"] == []
    assert _status(svc) == "ACTIVE"


def test_a_cast_that_fails_puts_the_status_and_the_claim_back(wired):
    svc, client, state, _ = wired
    _server(svc)
    state["cast_error"] = RuntimeError("rabbitmq is down")

    r = client.post("/servers/srv1/live-migrate", json={"host": "node-2"})

    assert r.status_code == 503, r.text
    assert "rabbitmq is down" in r.json()["detail"]
    assert _status(svc) == "ACTIVE"
    assert _host(svc) == "node-1"
    assert state["restores"] == ["node-1"]
    action, = client.get("/servers/srv1/os-instance-actions").json()["instanceActions"]
    assert (action["action"], action["result"]) == ("live_migrate", "error")


def _finish(client, result):
    return client.post("/internal/servers/srv1/actions/finish",
                       json={"result": result, "action": "live_migrate", "message": "boom" if result == "error" else None},
                       headers={"X-Internal-Token": "service-token"})


@pytest.fixture
def internal(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "service-token")


def test_finish_with_error_restores_the_claim_on_the_source_host(wired, internal):
    svc, client, state, _ = wired
    _server(svc)

    r = _finish(client, "error")

    assert r.status_code == 200, r.text
    assert state["restores"] == ["node-1"]


def test_finish_with_success_leaves_the_claim_on_the_target(wired, internal):
    svc, client, state, _ = wired
    _server(svc, host="node-2")

    r = _finish(client, "success")

    assert r.status_code == 200, r.text
    assert state["restores"] == []


def test_a_placement_error_on_finish_is_logged_and_the_answer_is_still_ok(wired, internal, monkeypatch):
    svc, client, state, ops = wired
    _server(svc)

    def broken(db, server):
        raise RuntimeError("placement exploded")

    monkeypatch.setattr(ops, "restore_source_claim", broken)

    r = _finish(client, "error")

    assert r.status_code == 200, r.text


class _Reply:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.text = ""

    def json(self):
        return {"host": "node-1"}


def test_restore_source_claim_asks_placement_for_the_source_only_even_when_it_is_disabled(api, monkeypatch):
    """A drain disables the source before it live-migrates; claim_host would filter the source out and never put the
    allocation back, so the restore names the source as the only host and excludes none."""
    svc, _, _, _ = api
    for key in PEERS:
        monkeypatch.setenv(key, "http://peer.test:1")
    _server(svc)
    _service(svc, "node-1", disabled=True)
    ops = importlib.import_module("services.compute.app.routers.ops")
    sent = []
    monkeypatch.setattr(ops.requests, "post", lambda url, **kw: sent.append((url, kw["json"])) or _Reply())

    with svc.SessionLocal() as db:
        ops.restore_source_claim(db, db.get(svc.Server, "srv1"))

    (url, body), = sent
    assert url.endswith("/internal/claim")
    assert body == {"consumer_uuid": "srv1", "project_id": "p1", "resources": {"VCPU": 1, "MEMORY_MB": 512},
                    "exclude_hosts": [], "only_hosts": ["node-1"]}
