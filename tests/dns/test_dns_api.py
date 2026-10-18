import importlib

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth, require_internal


@pytest.fixture
def dns(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'dns.sqlite'}")
    monkeypatch.setenv("DNS_PLATFORM_DOMAIN", "cloud.lab")
    import services.dns.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    with m.SessionLocal() as db:
        m.seed(db)
    who = {"project_id": "p1", "role": "member"}
    m.app.dependency_overrides[require_auth] = lambda: who
    m.app.dependency_overrides[require_internal] = lambda: None
    monkeypatch.setattr(m.requests, "get", lambda *a, **k: (_ for _ in ()).throw(m.requests.ConnectionError("down")))
    c = TestClient(m.app)
    c.who, c.m = who, m
    return c


def zone(c, name="example.com", **kw):
    return c.post("/zones", json={"name": name, **kw})


def test_zone_lifecycle_and_visibility(dns):
    r = zone(dns, "Example.COM.")
    assert r.status_code == 201 and r.json()["name"] == "example.com" and r.json()["status"] == "PENDING"
    assert zone(dns).status_code == 409
    dns.who.update(project_id="p2")
    assert zone(dns).status_code == 409
    assert dns.get("/zones/example.com").status_code == 404
    assert "example.com" not in [z["name"] for z in dns.get("/zones").json()["zones"]]
    dns.who.update(project_id="p1")
    assert dns.delete("/zones/example.com").status_code == 204


def test_reserved_and_invalid_names(dns):
    for n in ("cloud.lab", "x.cloud.lab", "internal.cloud.lab"):
        assert zone(dns, n).status_code == 400
    assert zone(dns, "bad name.com").status_code == 422
    assert dns.delete("/zones/internal.cloud.lab").status_code == 403
    assert dns.post("/zones/elb.cloud.lab/recordsets", json={"name": "x", "type": "A", "records": ["1.1.1.1"]}).status_code == 403
    assert len([z for z in dns.get("/zones").json()["zones"] if z["kind"] == "platform"]) == 5


def test_recordsets_and_cname_rules(dns):
    zone(dns)
    p = "/zones/example.com/recordsets"
    a = dns.post(p, json={"name": "www", "type": "a", "records": ["1.2.3.4"]})
    assert a.status_code == 201 and a.json()["status"] == "PENDING"
    assert dns.post(p, json={"name": "www", "type": "A", "records": ["1.2.3.5"]}).status_code == 409
    assert dns.post(p, json={"name": "www", "type": "CNAME", "records": ["x.com"]}).status_code == 400
    assert dns.post(p, json={"name": "@", "type": "CNAME", "records": ["x.com"]}).status_code == 400
    assert dns.post(p, json={"name": "@", "type": "NS", "records": ["ns.x.com"]}).status_code == 400
    assert dns.post(p, json={"name": "m", "type": "A", "records": ["nope"]}).status_code == 400
    assert dns.post(p, json={"name": "m", "type": "SOA", "records": ["x"]}).status_code == 400
    u = dns.put(f"{p}/{a.json()['id']}", json={"records": ["9.9.9.9"]})
    assert u.json()["records"] == ["9.9.9.9"]
    assert dns.delete(f"{p}/{a.json()['id']}").status_code == 204


def test_serial_bump_and_status_race(dns):
    z = zone(dns).json()
    dns.post("/zones/example.com/recordsets", json={"name": "a", "type": "A", "records": ["1.1.1.1"]})
    cur = dns.get("/zones/example.com").json()
    assert cur["serial"] > z["serial"]
    dns.post(f"/internal/zones/{z['id']}/status", json={"serial": z["serial"]})
    assert dns.get("/zones/example.com").json()["status"] == "PENDING"
    dns.post(f"/internal/zones/{z['id']}/status", json={"serial": cur["serial"]})
    assert dns.get("/zones/example.com").json()["status"] == "ACTIVE"


def test_registered_records_are_managed_and_replaced(dns):
    body = {"owner": "floating_ip:f1", "project_id": "p1", "fqdn": "203-0-113-10.compute.cloud.lab", "records": ["203.0.113.10"]}
    assert dns.post("/internal/records", json=body).status_code == 200
    assert dns.post("/internal/records", json=body).status_code == 200
    rs = dns.get("/zones/compute.cloud.lab/recordsets").json()["recordsets"]
    assert len(rs) == 1 and rs[0]["managed_by"] == "floating_ip:f1"
    dns.who.update(project_id="p1")
    assert dns.put(f"/zones/compute.cloud.lab/recordsets/{rs[0]['id']}", json={"records": ["1.1.1.1"]}).status_code == 403
    assert dns.post("/internal/records", json={**body, "fqdn": "evil.example.com"}).status_code == 400
    assert dns.post("/internal/records", json={**body, "type": "TXT"}).status_code == 400
    assert dns.post("/internal/records", json={**body, "fqdn": "x.internal.cloud.lab"}).status_code == 400
    assert dns.delete("/internal/records", params={"owner": "floating_ip:f1"}).status_code == 204
    assert dns.get("/zones/compute.cloud.lab/recordsets").json()["recordsets"] == []
    assert dns.get("/internal/records").json()["owners"] == []


def test_user_cannot_edit_managed_recordset_inside_own_zone(dns):
    z = zone(dns).json()
    with dns.m.SessionLocal() as db:
        db.add(dns.m.RecordSet(id="r1", zone_id=z["id"], name="x", type="A", records=["1.1.1.1"], managed_by="server:s1"))
        db.commit()
    assert dns.delete("/zones/example.com/recordsets/r1").status_code == 409


def test_internal_zone_in_state_and_secondaries(dns):
    zone(dns, "pub.org")
    s = dns.post("/zones/pub.org/secondaries", json={"address": "203.0.113.5"})
    assert s.status_code == 201
    assert dns.post("/zones/pub.org/secondaries", json={"address": "203.0.113.5"}).status_code == 409
    assert dns.post("/zones/pub.org/secondaries", json={"address": "127.0.0.1"}).status_code == 422
    assert dns.post("/zones/pub.org/secondaries", json={"address": "x;y"}).status_code == 422
    zone(dns, "priv.org", scope="internal")
    assert dns.post("/zones/priv.org/secondaries", json={"address": "203.0.113.6"}).status_code == 400
    st = {z["name"]: z for z in dns.get("/internal/state").json()["zones"]}
    assert st["pub.org"]["secondaries"] == ["203.0.113.5"] and st["priv.org"]["scope"] == "internal"
    assert {"name": "ns1", "type": "A", "ttl": None, "records": ["172.20.0.120"]} in st["cloud.lab"]["recordsets"]
    assert dns.delete(f"/zones/pub.org/secondaries/{s.json()['id']}").status_code == 204


def test_quota(dns, monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self): return {"max_zones": 1, "max_recordsets": 1, "domain": {}}
    monkeypatch.setattr(dns.m.requests, "get", lambda *a, **k: R())
    assert zone(dns, "a.org").status_code == 201
    assert zone(dns, "b.org").status_code == 413
    assert dns.post("/zones/a.org/recordsets", json={"name": "x", "type": "A", "records": ["1.1.1.1"]}).status_code == 201
    assert dns.post("/zones/a.org/recordsets", json={"name": "y", "type": "A", "records": ["1.1.1.1"]}).status_code == 413
    dns.post("/internal/records", json={"owner": "floating_ip:f9", "fqdn": "1-1-1-1.compute.cloud.lab", "records": ["1.1.1.1"]})
    assert zone(dns, "c.org").status_code == 413  # managed records and platform zones do not count; zone cap still holds