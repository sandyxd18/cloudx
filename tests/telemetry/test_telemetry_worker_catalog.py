from types import SimpleNamespace

import pytest
from services.telemetry_worker.app import main as w
from shared import catalog


def _down(service, interface="internal"):
    raise catalog.CatalogError(f"cannot resolve service '{service}'")


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    for key in ("COMPUTE_API_URL", "TELEMETRY_API_URL"):
        monkeypatch.delenv(key, raising=False)


def test_instances_come_from_the_compute_host_in_the_catalog(monkeypatch):
    seen = []
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8003/")
    monkeypatch.setattr(w, "_api", lambda method, url, **kw: seen.append(url) or SimpleNamespace(
        status_code=200, json=lambda: {"servers": [{"id": "s1", "name": "vm", "project_id": "p"}]}))
    assert w._get_active_instances() == [{"id": "s1", "name": "vm", "project_id": "p"}]
    assert seen == ["http://catalog-host:8003/servers?all_tenants=true&status=ACTIVE"]


def test_instances_are_read_from_the_items_list_compute_api_really_returns(monkeypatch):
    # GET /servers answers {"items": [...]}; reading only a "servers" key found no instance and no metric was ever sent
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8003/")
    monkeypatch.setattr(w, "_api", lambda method, url, **kw: SimpleNamespace(
        status_code=200, json=lambda: {"items": [{"id": "s1", "name": "vm", "project_id": "p", "status": "ACTIVE"}]}))
    assert w._get_active_instances() == [{"id": "s1", "name": "vm", "project_id": "p"}]


def test_only_this_nodes_instances_are_sampled_and_the_libvirt_domain_name_is_kept(monkeypatch):
    # libvirt names domains mini-os-<name>-<id8>, not the server id or name, so lookups need libvirt_domain_name
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8003/")
    monkeypatch.setattr(w, "COMPUTE_HOST", "infra03")
    items = [
        {"id": "s1", "name": "vm", "project_id": "p", "host": "infra03", "libvirt_domain_name": "mini-os-vm-s1"},
        {"id": "s2", "name": "other", "project_id": "p", "host": "infra02", "libvirt_domain_name": "mini-os-other-s2"},
    ]
    monkeypatch.setattr(w, "_api", lambda method, url, **kw: SimpleNamespace(status_code=200, json=lambda: {"items": items}))
    assert w._get_active_instances() == [{"id": "s1", "name": "vm", "project_id": "p", "domain": "mini-os-vm-s1"}]


def test_unconfigured_compute_host_samples_every_instance(monkeypatch):
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8003/")
    monkeypatch.setattr(w, "COMPUTE_HOST", "single-node")
    items = [{"id": "s1", "name": "a", "project_id": "p", "host": "node-a"}, {"id": "s2", "name": "b", "project_id": "p", "host": "node-b"}]
    monkeypatch.setattr(w, "_api", lambda method, url, **kw: SimpleNamespace(status_code=200, json=lambda: {"items": items}))
    assert [i["id"] for i in w._get_active_instances()] == ["s1", "s2"]


def test_domain_is_looked_up_by_its_libvirt_name_first(monkeypatch):
    looked_up = []

    class Conn:
        def lookupByName(self, name):
            looked_up.append(("name", name))
            return object()

        def lookupByUUIDString(self, uuid):
            looked_up.append(("uuid", uuid))
            raise RuntimeError("no such domain")

        def close(self):
            pass

    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://catalog-host:8010/")
    monkeypatch.setattr(w, "_api", lambda *a, **k: SimpleNamespace(status_code=200))
    monkeypatch.setattr(w, "_get_active_instances", lambda: [{"id": "s1", "name": "vm", "project_id": "p", "domain": "mini-os-vm-s1"}])
    monkeypatch.setattr(w, "_get_libvirt_conn", lambda: Conn())
    monkeypatch.setattr(w, "_get_instance_stats_libvirt", lambda dom, iid: {"cpu_percent": 42.0})
    w.collect_and_send()
    assert looked_up == [("name", "mini-os-vm-s1")]


def test_compute_override_wins_over_a_failing_catalog(monkeypatch):
    monkeypatch.setenv("COMPUTE_API_URL", "http://override-host:1")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = []
    monkeypatch.setattr(w, "_api", lambda method, url, **kw: seen.append(url) or SimpleNamespace(
        status_code=200, json=lambda: {"servers": []}))
    w._get_active_instances()
    assert seen == ["http://override-host:1/servers?all_tenants=true&status=ACTIVE"]


def test_catalog_failure_falls_back_to_libvirt_like_an_unreachable_compute_api(monkeypatch):
    monkeypatch.setattr(catalog, "url", _down)
    monkeypatch.setattr(w, "_api", lambda *a, **k: pytest.fail("no request without a resolved URL"))

    class Conn:
        def listDomainsID(self):
            return [1]

        def lookupByID(self, i):
            return SimpleNamespace(name=lambda: "vm1", UUIDString=lambda: "u1")

        def close(self):
            pass

    monkeypatch.setattr(w, "_get_libvirt_conn", lambda: Conn())
    assert w._get_active_instances() == [{"id": "u1", "name": "vm1", "project_id": None}]


def test_batches_go_to_the_catalog_host_and_resolve_once_per_cycle(monkeypatch):
    asked, sent = [], []
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": asked.append(service) or "http://catalog-host:8010/")
    monkeypatch.setattr(w, "_api", lambda method, url, **kw: sent.append(url) or SimpleNamespace(status_code=200))
    monkeypatch.setattr(w, "_get_active_instances", lambda: [{"id": "i", "name": "i", "project_id": "p"}])
    monkeypatch.setattr(w, "_get_libvirt_conn", lambda: None)
    monkeypatch.setattr(w, "_get_instance_stats_shell", lambda name, iid: {f"m{n}_percent": 1.0 for n in range(250)})
    w.collect_and_send()
    assert sent == ["http://catalog-host:8010/telemetry/metrics/batch"] * 3
    assert asked == ["telemetry"]


def test_catalog_failure_while_sending_is_logged_once_per_cycle(monkeypatch, caplog):
    asked = []

    def down(service, interface="internal"):
        asked.append(service)
        raise catalog.CatalogError(f"cannot resolve service '{service}'")

    monkeypatch.setattr(catalog, "url", down)
    monkeypatch.setattr(w, "_api", lambda *a, **k: pytest.fail("no request without a resolved URL"))
    monkeypatch.setattr(w, "_get_active_instances", lambda: [{"id": "i", "name": "i", "project_id": "p"}])
    monkeypatch.setattr(w, "_get_libvirt_conn", lambda: None)
    monkeypatch.setattr(w, "_get_instance_stats_shell", lambda name, iid: {f"m{n}_percent": 1.0 for n in range(250)})
    w.collect_and_send()
    assert asked == ["telemetry"]
    assert "cannot resolve service 'telemetry'" in caplog.text


def test_cleanup_with_a_catalog_failure_only_logs(monkeypatch, caplog):
    monkeypatch.setattr(catalog, "url", _down)
    w._cleanup_old_metrics()
    assert "cannot resolve service 'telemetry'" in caplog.text
