"""Domain quota caps + domain-admin project visibility."""
from shared import domain
from tests.auth.test_domain_names import auth, login  # noqa: F401,F811


def _tok(a, **kw):
    return {"X-Auth-Token": login(a, **kw).json()["token"]}


def test_domain_quota_and_projects(auth, monkeypatch):  # noqa: F811
    sys_h = _tok(auth, username="sys", password="a")
    assert auth.client.get("/v3/domains/d1/quota", headers=sys_h).json()["max_instances"] is None
    r = auth.client.put("/v3/domains/d1/quota", json={"max_instances": 3}, headers=sys_h)
    assert r.json()["max_instances"] == 3
    da_h = _tok(auth, username="da1", password="a", domain="d1")
    assert auth.client.put("/v3/domains/d1/quota", json={"max_instances": 9}, headers=da_h).status_code == 403
    assert auth.client.get("/v3/domains/default/quota", headers=da_h).status_code == 404
    q = auth.client.get("/projects/p1/quota", headers=sys_h).json()["domain"]
    assert q["caps"] == {"max_instances": 3} and q["project_ids"] == ["p1"]
    assert auth.client.delete("/v3/domains/d1/quota", headers=sys_h).status_code == 204
    assert auth.client.get("/projects/p1/quota", headers=sys_h).json()["domain"]["caps"] == {}


def test_visible_projects(monkeypatch):
    monkeypatch.setattr(domain, "domain_projects", lambda d: ["a", "b"] if d == "d1" else ["z"])
    assert domain.visible_projects({"role": "admin"}) is None
    assert domain.visible_projects({"role": "admin"}, "d1") == ["a", "b"]
    assert domain.visible_projects({"role": "member", "domain_admin": True, "domain_id": "d1"}) == ["a", "b"]
    assert domain.visible_projects({"role": "member", "domain_admin": True, "domain_id": "d1"}, "other") == []
    assert domain.visible_projects({"role": "member", "project_id": "a"}) == ["a"]
    assert not domain.can_see({"role": "member", "project_id": "a"}, "b")
    assert domain.can_see({"role": "member", "domain_admin": True, "domain_id": "d1"}, "b")
def test_dns_quota_fields_roundtrip(auth):  # noqa: F811
    sys_h = _tok(auth, username="sys", password="a")
    q = auth.client.get("/projects/p1/quota", headers=sys_h).json()
    assert q["max_zones"] == 10 and q["max_recordsets"] == 200
    assert auth.client.put("/projects/p1/quota", json={"max_zones": 2}, headers=sys_h).json()["max_zones"] == 2
    assert auth.client.put("/v3/domains/d1/quota", json={"max_recordsets": 5}, headers=sys_h).json()["max_recordsets"] == 5
    assert auth.client.get("/projects/p1/quota", headers=sys_h).json()["domain"]["caps"] == {"max_recordsets": 5}

def test_internal_project_names(auth):  # noqa: F811
    r = auth.client.get("/internal/projects")
    assert r.status_code == 200 and "p1" in r.json()["projects"]