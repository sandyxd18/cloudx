from types import SimpleNamespace

import pytest
from services.orchestration_worker.app import main as w
from shared import catalog

GROUP = {"id": "g1", "name": "web", "flavor_id": "f", "image_id": "i", "network_id": "n", "target_cpu_percent": 50,
         "cooldown_seconds": 0, "scale_in_cooldown_seconds": 0, "min_instances": 1, "max_instances": 3,
         "load_balancer_pool_id": "pool1"}


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    for key in ("ORCHESTRATION_API_URL", "COMPUTE_API_URL", "TELEMETRY_API_URL", "LOAD_BALANCER_API_URL"):
        monkeypatch.delenv(key, raising=False)


def _hosts(monkeypatch, **hosts):
    def url(service, interface="internal"):
        if service not in hosts:
            raise catalog.CatalogError(f"cannot resolve service '{service}'")
        return hosts[service]

    monkeypatch.setattr(catalog, "url", url)


def test_calls_go_to_the_catalog_hosts(monkeypatch):
    _hosts(monkeypatch, orchestration="http://o:8013/", compute="http://c:8000", telemetry="http://t:8010", **{"load-balancer": "http://l:8008"})
    seen = []
    monkeypatch.setattr(w.requests, "get", lambda url, **kw: seen.append(url) or SimpleNamespace(
        status_code=200, json=lambda: [{"value": 7.0}]))
    w._get_scaling_groups()
    w._get_server("s1")
    assert w._get_instance_avg_cpu(["s1"]) == 7.0
    assert seen[0] == "http://o:8013/scaling-groups?active_only=true"
    assert seen[1] == "http://c:8000/servers/s1"
    assert seen[2].startswith("http://t:8010/telemetry/metrics?instance_id=s1")


def test_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("COMPUTE_API_URL", "http://override-host:1/")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = []
    monkeypatch.setattr(w.requests, "get", lambda url, **kw: seen.append(url) or SimpleNamespace(
        status_code=200, json=lambda: {"id": "s1"}))
    w._get_server("s1")
    assert seen == ["http://override-host:1/servers/s1"]


def test_unresolvable_peers_behave_like_unreachable_ones(monkeypatch, caplog):
    _hosts(monkeypatch)
    monkeypatch.setattr(w.requests, "get", lambda *a, **k: pytest.fail("no request without a resolved URL"))
    assert w._get_scaling_groups() == []
    assert w._get_server("s1") is None
    assert w._get_instance_avg_cpu(["s1", "s2"]) is None
    assert "cannot resolve service 'orchestration'" in caplog.text


def test_scale_out_resolves_every_peer_before_spawning(monkeypatch, caplog):
    _hosts(monkeypatch, orchestration="http://o:8013", telemetry="http://t:8010", compute="http://c:8000")  # no load-balancer
    monkeypatch.setattr(w, "_get_group_instances", lambda gid: [{"status": "active", "server_id": "s1"}])
    monkeypatch.setattr(w, "_get_instance_avg_cpu", lambda ids: 90.0)
    monkeypatch.setattr(w.requests, "post", lambda *a, **k: pytest.fail("nothing may be spawned or recorded"))
    monkeypatch.setattr(w.requests, "delete", lambda *a, **k: pytest.fail("nothing may be terminated"))
    w._check_group(GROUP)
    assert "cannot resolve service 'load-balancer'" in caplog.text


def test_scale_in_resolves_every_peer_before_draining(monkeypatch, caplog):
    _hosts(monkeypatch, orchestration="http://o:8013", telemetry="http://t:8010", **{"load-balancer": "http://l:8008"})  # no compute
    instances = [{"status": "active", "server_id": f"s{n}", "created_at": str(n)} for n in range(2)]
    monkeypatch.setattr(w, "_get_group_instances", lambda gid: instances)
    monkeypatch.setattr(w, "_get_instance_avg_cpu", lambda ids: 1.0)
    monkeypatch.setattr(w.requests, "delete", lambda *a, **k: pytest.fail("nothing may be drained or terminated"))
    w._check_group(GROUP)
    assert "cannot resolve service 'compute'" in caplog.text
