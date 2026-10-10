import importlib

from fastapi.testclient import TestClient


def test_claim_picks_fitting_node_honours_filters_and_releases(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'claim.sqlite'}")
    import services.placement.app.main as m

    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    from shared.auth_middleware import require_internal
    m.app.dependency_overrides[require_internal] = lambda: None
    c = TestClient(m.app)

    for name, ram in (("small", 2048), ("big", 16384)):
        assert c.post("/internal/resource_providers", json={"name": name}).status_code == 201
        inv = {"VCPU": {"total": 4, "max_unit": 4, "allocation_ratio": 2.0},
               "MEMORY_MB": {"total": ram, "max_unit": ram, "allocation_ratio": 1.0}}
        assert c.put(f"/internal/resource_providers/{name}/inventories", json={"inventories": inv}).status_code == 200

    def claim(cid, **kw):
        return c.post("/internal/claim", json={"consumer_uuid": cid, "project_id": "p", "resources": {"VCPU": 2, "MEMORY_MB": 1024}, **kw})

    assert claim("a").json()["host"] == "big"                                  # most free RAM
    assert claim("b", exclude_hosts=["big"]).json()["host"] == "small"         # anti-affinity
    assert claim("c", only_hosts=["small"]).json()["host"] == "small"          # affinity
    assert claim("d", only_hosts=["small"]).status_code == 409                 # small RAM exhausted
    assert c.delete("/internal/allocations/b").status_code == 204
    assert claim("d", only_hosts=["small"]).json()["host"] == "small"          # freed capacity is reusable
