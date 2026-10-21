from types import SimpleNamespace

import pytest
from services.container_worker.app import main as w
from shared import catalog


def _down(service, interface="internal"):
    raise catalog.CatalogError(f"cannot resolve service '{service}'")


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def get(url, **kw):
        seen.append(("GET", url))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"containers": []})

    monkeypatch.setattr(w.requests, "get", get)
    monkeypatch.setattr(w.requests, "post", lambda url, **kw: seen.append(("POST", url)))
    monkeypatch.setattr(w, "HOST", "node-1")
    monkeypatch.setattr(w, "docker_state", lambda: {})
    return seen


def test_tick_reaches_container_api_through_the_catalog(monkeypatch, calls):
    monkeypatch.delenv("CONTAINER_API_URL", raising=False)
    asked = []
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": asked.append(service) or "http://catalog-host:8018/")
    w.tick()
    assert calls == [("GET", "http://catalog-host:8018/internal/ct-agent/node-1")]
    assert asked == ["container"]


def test_env_override_still_wins(monkeypatch, calls):
    monkeypatch.setenv("CONTAINER_API_URL", "http://override-host:1")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    w.tick()
    assert calls == [("GET", "http://override-host:1/internal/ct-agent/node-1")]


def test_catalog_failure_fails_the_tick_before_any_work_and_the_loop_survives(monkeypatch, calls, caplog, tmp_path):
    monkeypatch.delenv("CONTAINER_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", _down)
    monkeypatch.setattr(w, "docker_state", lambda: pytest.fail("no work before the peer is resolved"))
    sleeps = []

    def sleep(_):
        sleeps.append(1)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(w.time, "sleep", sleep)
    monkeypatch.setattr(w, "STATE", tmp_path / "state")
    with pytest.raises(KeyboardInterrupt):
        w.main()
    assert caplog.text.count("tick failed: cannot resolve service 'container'") == 2
    assert calls == []
