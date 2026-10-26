import importlib
import sys

from shared import dns_names


def worker():
    sys.modules.setdefault("shared.proc", importlib.import_module("shared.proc"))
    return importlib.import_module("services.dns_worker.app.main")


def w(owner, fqdn, ip):
    return {"owner": owner, "fqdn": fqdn, "records": [ip], "type": "A", "scope": "internal"}


def test_sweep_creates_missing_changed_and_removes_orphans():
    m = worker()
    want = [w("server:1", "a.p.internal.cloud.lab", "10.0.0.5"), w("server:2", "b.p.internal.cloud.lab", "10.0.0.6")]
    have = {"server:2": {"fqdn": "b.p.internal.cloud.lab", "records": ["10.0.0.9"]},
            "server:3": {"fqdn": "gone.p.internal.cloud.lab", "records": ["10.0.0.7"]},
            "bucket:9": {"fqdn": "x.s3.cloud.lab", "records": ["1.1.1.1"]}}
    todo, gone = m.sweep(want, have)
    assert [t["owner"] for t in todo] == ["server:1", "server:2"]
    assert gone == ["server:3"]  # buckets are not swept
    assert m.sweep(want, {"server:1": {"fqdn": want[0]["fqdn"], "records": ["10.0.0.5"]},
                          "server:2": {"fqdn": want[1]["fqdn"], "records": ["10.0.0.6"]}}) == ([], [])


def test_register_never_raises(monkeypatch):
    def boom(*a, **k):
        raise dns_names.requests.ConnectionError("down")
    monkeypatch.setattr(dns_names.requests, "post", boom)
    monkeypatch.setattr(dns_names.requests, "delete", boom)
    dns_names.register("floating_ip:1", "p", "1-1-1-1.compute.cloud.lab", "A", ["1.1.1.1"])
    dns_names.unregister("floating_ip:1")



def test_sweep_keeps_the_records_of_a_source_that_could_not_be_read():
    m = worker()
    have = {"load_balancer:1": {"fqdn": "lb-1.elb.cloud.lab", "records": ["1.1.1.1"]},
            "load_balancer_internal:1": {"fqdn": "lb.p.internal.cloud.lab", "records": ["10.0.0.4"]},
            "server:3": {"fqdn": "gone.p.internal.cloud.lab", "records": ["10.0.0.7"]}}
    _, gone = m.sweep([], have, keep_kinds=("load_balancer", "load_balancer_internal"))
    assert gone == ["server:3"]  # a down load balancer API must not wipe the load balancer names
    assert m.sweep([], have)[1] == ["load_balancer:1", "load_balancer_internal:1", "server:3"]
