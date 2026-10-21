import pytest
from services.compute_worker.app import image_manager, main, worker
from shared import catalog

PEERS = ("IMAGE_API_URL", "COMPUTE_API_URL", "PLACEMENT_API_URL", "HA_API_URL")


def _down(service, interface="internal"):
    raise catalog.CatalogError(f"cannot resolve service '{service}'")


def _hosts(monkeypatch, **by_service):
    asked = []

    def fake(service, interface="internal"):
        asked.append(service)
        if service not in by_service:
            raise catalog.CatalogError(f"cannot resolve service '{service}'")
        return by_service[service]

    monkeypatch.setattr(catalog, "url", fake)
    return asked


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    for key in PEERS:
        monkeypatch.delenv(key, raising=False)


class Resp:
    status_code = 200

    def __init__(self, body=None):
        self._body = body or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=0):
        return iter([b"data"])


def test_image_metadata_and_download_go_to_the_catalog_host(monkeypatch, tmp_path):
    asked = _hosts(monkeypatch, image="http://img-host:8002/")
    monkeypatch.setattr(image_manager, "IMAGE_CACHE_DIR", tmp_path)
    seen = []
    monkeypatch.setattr(image_manager.requests, "get", lambda url, **kw: seen.append(url) or Resp({"id": "i1"}))
    assert image_manager.fetch_image_meta("i1") == {"id": "i1"}
    image_manager.download_image({"id": "i1", "filename": "a.qcow2"})
    assert seen == ["http://img-host:8002/images/i1", "http://img-host:8002/images/i1/download"]
    assert asked == ["image", "image"]


def test_a_cached_image_needs_no_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "url", lambda *a, **k: pytest.fail("cached: nothing to download"))
    monkeypatch.setattr(image_manager, "IMAGE_CACHE_DIR", tmp_path)
    (tmp_path / "i1.qcow2").write_bytes(b"x")
    assert image_manager.download_image({"id": "i1", "filename": "a.qcow2"}) == tmp_path / "i1.qcow2"


def test_worker_fetch_image_goes_to_the_catalog_host(monkeypatch):
    _hosts(monkeypatch, image="http://img-host:8002")
    seen = []
    monkeypatch.setattr(worker.requests, "get", lambda url, **kw: seen.append(url) or Resp({"id": "i1"}))
    worker.fetch_image("i1")
    assert seen == ["http://img-host:8002/images/i1"]


def test_prefetch_with_a_catalog_failure_answers_an_error_status(monkeypatch):
    monkeypatch.setattr(catalog, "url", _down)
    res = main.handle_prefetch_image({"image_id": "i1"})
    assert res["status"] == "error" and "cannot resolve service 'image'" in res["message"]


def test_image_override_still_wins(monkeypatch):
    monkeypatch.setenv("IMAGE_API_URL", "http://override-host:1")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = []
    monkeypatch.setattr(image_manager.requests, "get", lambda url, **kw: seen.append(url) or Resp({"id": "i1"}))
    image_manager.fetch_image_meta("i1")
    assert seen == ["http://override-host:1/images/i1"]


def test_heartbeat_goes_to_the_catalog_host_and_never_raises_on_a_catalog_failure(monkeypatch):
    from services.compute_worker.app import iscsi

    monkeypatch.setattr(iscsi, "initiator_iqn", lambda: "iqn.me")
    posts = []
    monkeypatch.setattr(main.requests, "post", lambda url, **kw: posts.append(url))
    _hosts(monkeypatch, ha="http://ha-host:8020/")
    main.send_heartbeat()
    assert posts == [f"http://ha-host:8020/internal/heartbeat/{main.COMPUTE_HOST}"]
    posts.clear()
    monkeypatch.setattr(catalog, "url", _down)
    main.send_heartbeat()  # must not raise
    assert posts == []


def test_notify_action_finish_goes_to_the_catalog_host_and_survives_a_failure(monkeypatch, caplog):
    posts = []
    monkeypatch.setattr(main.requests, "post", lambda url, **kw: posts.append(url))
    _hosts(monkeypatch, compute="http://compute-host:8003/")
    main._notify_action_finish("s1")
    assert posts == ["http://compute-host:8003/internal/servers/s1/actions/finish"]
    monkeypatch.setattr(catalog, "url", _down)
    main._notify_action_finish("s1")
    assert "could not notify compute-api" in caplog.text and len(posts) == 1


def test_reconcile_stale_domains_survives_a_catalog_failure(monkeypatch, caplog):
    monkeypatch.setattr(main.libvirt_driver, "get_all_domain_states", lambda: {"dom1": "running"})
    destroyed = []
    monkeypatch.setattr(main.libvirt_driver, "delete", lambda name: destroyed.append(name))
    seen = []
    monkeypatch.setattr(main.requests, "get", lambda url, **kw: seen.append(url) or Resp({"servers": []}))
    _hosts(monkeypatch, compute="http://compute-host:8003/")
    main.reconcile_stale_domains()
    assert seen == ["http://compute-host:8003/internal/servers"]
    monkeypatch.setattr(catalog, "url", _down)
    main.reconcile_stale_domains()
    assert "could not fetch servers from API" in caplog.text and destroyed == [] and len(seen) == 1


def test_placement_registration_retries_until_the_catalog_answers(monkeypatch):
    answers = iter([None, "http://placement-host:8006/"])  # catalog down on the first pass
    asked = []

    def url(service, interface="internal"):
        asked.append(service)
        found = next(answers)
        if found is None:
            raise catalog.CatalogError("cannot resolve service 'placement'")
        return found

    sleeps, posts = [], []
    monkeypatch.setattr(catalog, "url", url)
    monkeypatch.setattr(main.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(main.requests, "post", lambda u, **kw: posts.append(u) or Resp())
    monkeypatch.setattr(main.requests, "put", lambda u, **kw: posts.append(u) or Resp())
    monkeypatch.setenv("PLACEMENT_TOTAL_RAM_MB", "1024")
    main.placement_registration_loop()  # returns once registered
    assert asked == ["placement", "placement"] and sleeps == [10]
    assert posts[0] == "http://placement-host:8006/internal/resource_providers"
    assert posts[1] == f"http://placement-host:8006/internal/resource_providers/{main.COMPUTE_HOST}/inventories"
