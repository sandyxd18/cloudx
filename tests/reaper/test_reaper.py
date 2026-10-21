import pytest
import services.reaper.app.main as r
from shared import catalog

FILE_STORE, DATABASE, CONTAINER = "http://127.0.0.1:8017", "http://127.0.0.1:8019", "http://127.0.0.1:8018"


@pytest.fixture(autouse=True)
def _peers(monkeypatch):
    monkeypatch.setenv("FILE_STORE_API_URL", FILE_STORE)
    monkeypatch.setenv("DATABASE_API_URL", DATABASE + "/")
    monkeypatch.setenv("CONTAINER_API_URL", CONTAINER)


class Fake:
    """Stands in for requests: records DELETEs, serves stale-errors per service."""

    def __init__(self, stale, fail=()):
        self.stale, self.fail, self.deleted, self.login_ok = stale, set(fail), [], True

    def request(self, method, url, **kw):
        class R:
            def __init__(s, code, data=None):
                s.status_code, s._d = code, data or {}
            def json(s):
                return s._d
            def raise_for_status(s):
                if s.status_code >= 400:
                    raise RuntimeError(s.status_code)
        if url.endswith("/auth/tokens"):
            return R(201, {"token": "T"}) if self.login_ok else R(401)
        if "/internal/stale-errors" in url:
            return R(200, {"items": self.stale.get(url.split("/internal")[0], [])})
        if method == "DELETE":
            if any(url.endswith(f) for f in self.fail):
                return R(500)
            self.deleted.append(url)
            return R(202)
        raise AssertionError(url)


def run(monkeypatch, stale, dry=False, fail=()):
    f = Fake(stale, fail)
    monkeypatch.setattr(r.requests, "request", f.request)
    monkeypatch.setattr(r, "DRY_RUN", dry)
    r.cycle()
    return f


S = {FILE_STORE: [{"id": "sh1", "kind": "share"}, {"id": "sn1", "kind": "share-network"}],
     DATABASE: [{"id": "db1", "kind": "database"}],
     CONTAINER: [{"id": "c1", "kind": "container"}]}


def test_deletes_each_stale_row_through_the_public_delete(monkeypatch):
    f = run(monkeypatch, S)
    paths = [u.split(":", 2)[2].split("/", 1)[1] for u in f.deleted]
    assert sorted(paths) == ["containers/c1", "instances/db1", "share-networks/sn1", "shares/sh1"]


def test_shares_go_before_their_share_network(monkeypatch):
    f = run(monkeypatch, S)
    order = [u for u in f.deleted if "shares/" in u or "share-networks/" in u]
    assert order[0].endswith("shares/sh1") and order[1].endswith("share-networks/sn1")


def test_dry_run_deletes_nothing(monkeypatch):
    assert run(monkeypatch, S, dry=True).deleted == []


def test_one_failed_delete_does_not_stop_the_rest(monkeypatch):
    f = run(monkeypatch, S, fail=("instances/db1",))
    assert len(f.deleted) == 3


def test_failed_login_skips_the_whole_cycle(monkeypatch):
    f = Fake(S)
    f.login_ok = False
    monkeypatch.setattr(r.requests, "request", f.request)
    r.cycle()
    assert f.deleted == []


def test_scheduled_backups_are_created_and_expired_ones_deleted(monkeypatch):
    base = DATABASE
    calls = []

    def req(method, url, **kw):
        calls.append((method, url))
        class R:
            status_code = 200
            def raise_for_status(s): pass
            def json(s):
                if url.endswith("/auth/tokens"):
                    return {"token": "T"}
                if url.endswith("backup-due"):
                    return {"items": [{"id": "i1", "kind": "database"}]}
                if url.endswith("backup-expired"):
                    return {"items": [{"id": "b1", "kind": "backup"}]}
                return {"items": []}
        return R()
    monkeypatch.setattr(r.requests, "request", req)
    monkeypatch.setattr(r, "DRY_RUN", False)
    r.cycle()
    assert ("POST", f"{base}/instances/i1/backups") in calls and ("DELETE", f"{base}/backups/b1") in calls


def _catalog_only(monkeypatch, hosts, asked=None):
    for key in ("FILE_STORE_API_URL", "DATABASE_API_URL", "CONTAINER_API_URL"):
        monkeypatch.delenv(key)

    def fake(service, interface="internal"):
        if asked is not None:
            asked.append(service)
        if service not in hosts:
            raise catalog.CatalogError(f"cannot resolve service '{service}'")
        return hosts[service]

    monkeypatch.setattr(catalog, "url", fake)


def test_with_no_override_the_calls_go_to_the_catalog_hosts(monkeypatch):
    asked = []
    _catalog_only(monkeypatch, {"file-store": "http://fs-host:1/", "database": "http://db-host:2", "container": "http://ct-host:3"}, asked)
    stale = {"http://fs-host:1": [{"id": "sh1", "kind": "share"}], "http://db-host:2": [{"id": "db1", "kind": "database"}],
             "http://ct-host:3": [{"id": "c1", "kind": "container"}]}
    f = run(monkeypatch, stale)
    assert sorted(f.deleted) == ["http://ct-host:3/containers/c1", "http://db-host:2/instances/db1", "http://fs-host:1/shares/sh1"]
    assert asked == ["file-store", "database", "container"]  # once per cycle, before anything is touched


def test_the_override_wins_over_the_catalog(monkeypatch):
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    f = run(monkeypatch, S)
    assert len(f.deleted) == 4 and all(u.startswith("http://127.0.0.1:") for u in f.deleted)


def test_a_service_the_catalog_cannot_resolve_is_skipped_like_an_unreachable_one(monkeypatch, caplog):
    _catalog_only(monkeypatch, {"file-store": FILE_STORE, "container": CONTAINER})
    f = run(monkeypatch, S)
    assert sorted(u.split(":", 2)[2].split("/", 1)[1] for u in f.deleted) == ["containers/c1", "share-networks/sn1", "shares/sh1"]
    assert "cannot resolve service 'database'" in caplog.text


def test_cycle_survives_a_catalog_that_is_down_and_recovers(monkeypatch, caplog):
    _catalog_only(monkeypatch, {})
    f = run(monkeypatch, S)
    assert f.deleted == [] and "cannot resolve service" in caplog.text
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": {"file-store": FILE_STORE, "database": DATABASE, "container": CONTAINER}[service])
    assert len(run(monkeypatch, S).deleted) == 4
