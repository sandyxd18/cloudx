import logging
import threading
from types import SimpleNamespace

import pytest
import requests
from shared import catalog

ENV_KEYS = (
    "AUTH_API_URL", "IMAGE_API_URL", "NETWORKING_API_URL", "BLOCK_STORE_API_URL",
    "HA_API_URL", "INTERNAL_TOKEN", "CATALOG_CACHE_TTL", "CATALOG_RETRY_BACKOFF",
)


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _entry(name, **urls):
    return {
        "id": f"id-{name}",
        "name": name,
        "type": name,
        "endpoints": [
            {"id": f"{name}-{i}", "interface": i, "region": "RegionOne", "url": u}
            for i, u in urls.items()
        ],
    }


def _body(*entries):
    return {"catalog": list(entries)}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    catalog.invalidate()
    yield
    catalog.invalidate()


@pytest.fixture
def auth_url(monkeypatch):
    monkeypatch.setenv("AUTH_API_URL", "http://auth:8005")


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(catalog, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


@pytest.fixture
def fetches(monkeypatch):
    """Replaces requests.get; `fetches.queue` holds responses or exceptions, `fetches.calls` the calls."""
    state = SimpleNamespace(queue=[], calls=[])

    def fake_get(url, **kwargs):
        state.calls.append((url, kwargs))
        item = state.queue.pop(0) if len(state.queue) > 1 else state.queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(catalog.requests, "get", fake_get)
    return state


def test_override_wins(monkeypatch, fetches):
    monkeypatch.setenv("IMAGE_API_URL", "http://img:9002")
    assert catalog.url("image") == "http://img:9002"
    assert fetches.calls == []


def test_ha_override_with_several_urls_is_returned_verbatim(monkeypatch, fetches):
    monkeypatch.setenv("HA_API_URL", "http://a:8020,http://b:8020")
    assert catalog.url("ha") == "http://a:8020,http://b:8020"
    assert fetches.calls == []


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_override_counts_as_unset(monkeypatch, auth_url, fetches, blank):
    monkeypatch.setenv("IMAGE_API_URL", blank)
    fetches.queue = [FakeResponse(200, _body(_entry("image", internal="http://cat:8002")))]
    assert catalog.url("image") == "http://cat:8002"


def test_resolves_from_the_catalog_and_caches_for_the_ttl(monkeypatch, auth_url, clock, fetches):
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    fetches.queue = [FakeResponse(200, _body(_entry("image", internal="http://cat:8002", public="http://pub:8002")))]
    assert catalog.url("image") == "http://cat:8002"
    assert catalog.url("image", "public") == "http://pub:8002"
    assert len(fetches.calls) == 1
    url, kwargs = fetches.calls[0]
    assert url == "http://auth:8005/internal/catalog"
    assert kwargs["headers"] == {"X-Internal-Token": "tok"}
    assert kwargs["timeout"] == 3

    clock[0] += 59
    catalog.url("image")
    assert len(fetches.calls) == 1
    clock[0] += 2
    catalog.url("image")
    assert len(fetches.calls) == 2


def test_ttl_is_read_from_the_environment_at_call_time(monkeypatch, auth_url, clock, fetches):
    fetches.queue = [FakeResponse(200, _body(_entry("image", internal="http://cat:8002")))]
    monkeypatch.setenv("CATALOG_CACHE_TTL", "5")
    catalog.url("image")
    clock[0] += 6
    catalog.url("image")
    assert len(fetches.calls) == 2


@pytest.mark.parametrize("bad", ["abc", "-3", ""])
def test_invalid_ttl_falls_back_to_the_default(monkeypatch, auth_url, clock, fetches, bad):
    fetches.queue = [FakeResponse(200, _body(_entry("image", internal="http://cat:8002")))]
    monkeypatch.setenv("CATALOG_CACHE_TTL", bad)
    catalog.url("image")
    clock[0] += 59
    catalog.url("image")
    assert len(fetches.calls) == 1
    clock[0] += 2
    catalog.url("image")
    assert len(fetches.calls) == 2


def test_unreachable_auth_serves_the_stale_cache(auth_url, clock, fetches, caplog):
    fetches.queue = [
        FakeResponse(200, _body(_entry("image", internal="http://cat:8002"))),
        requests.ConnectionError("auth down"),
    ]
    assert catalog.url("image") == "http://cat:8002"
    clock[0] += 3600
    with caplog.at_level(logging.WARNING, logger="cloudx.catalog"):
        assert catalog.url("image") == "http://cat:8002"
    assert len(fetches.calls) == 2
    warnings = [r for r in caplog.records if r.name == "cloudx.catalog" and r.levelno == logging.WARNING]
    assert len(warnings) == 1


@pytest.mark.parametrize("failure", [
    requests.ConnectionError("auth down"),
    requests.Timeout("slow"),
    FakeResponse(503, {"detail": "busy"}),
    FakeResponse(200, ValueError("not json")),
    FakeResponse(200, {"nope": []}),
    FakeResponse(200, {"catalog": "x"}),
])
def test_unreachable_auth_without_cache_raises_catalog_error_naming_the_service(auth_url, fetches, failure):
    fetches.queue = [failure]
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.url("image")
    assert "image" in str(exc.value)
    if isinstance(failure, Exception):
        assert str(failure) in str(exc.value)
    elif failure.status_code != 200:
        assert str(failure.status_code) in str(exc.value)


def test_malformed_response_with_a_cache_serves_the_stale_cache(auth_url, clock, fetches):
    fetches.queue = [
        FakeResponse(200, _body(_entry("image", internal="http://cat:8002"))),
        FakeResponse(500, {}),
    ]
    catalog.url("image")
    clock[0] += 3600
    assert catalog.url("image") == "http://cat:8002"


def test_missing_auth_api_url_without_cache_raises_catalog_error(fetches):
    with pytest.raises(catalog.CatalogError, match="auth_api_url is not set") as exc:
        catalog.url("image")
    assert "image" in str(exc.value)
    assert fetches.calls == []


def test_identity_is_never_resolved_through_the_catalog(monkeypatch, fetches):
    with pytest.raises(catalog.CatalogError, match="auth_api_url is not set"):
        catalog.url("identity")
    assert fetches.calls == []
    monkeypatch.setenv("AUTH_API_URL", "http://auth:8005")
    assert catalog.url("identity") == "http://auth:8005"
    assert fetches.calls == []


def test_unknown_service_or_interface_raises_catalog_error(auth_url, fetches):
    with pytest.raises(catalog.CatalogError, match="unknown service 'nope'"):
        catalog.url("nope")
    fetches.queue = [FakeResponse(200, _body(_entry("image", internal="http://cat:8002")))]
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.url("image", "public")
    assert "image" in str(exc.value) and "public" in str(exc.value)
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.url("network")
    assert "network" in str(exc.value)


def test_invalidate_forces_a_refetch(auth_url, fetches):
    fetches.queue = [FakeResponse(200, _body(_entry("image", internal="http://cat:8002")))]
    catalog.url("image")
    catalog.invalidate()
    catalog.url("image")
    assert len(fetches.calls) == 2


def test_concurrent_callers_share_the_cache(auth_url, fetches):
    fetches.queue = [FakeResponse(200, _body(_entry("image", internal="http://cat:8002")))]
    results = []
    threads = [threading.Thread(target=lambda: results.append(catalog.url("image"))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["http://cat:8002"] * 8


@pytest.fixture
def sent(monkeypatch):
    state = SimpleNamespace(queue=[], calls=[])

    def fake_request(method, url, **kwargs):
        state.calls.append((method, url, kwargs))
        item = state.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(catalog.requests, "request", fake_request)
    return state


def test_request_uses_the_resolved_url_and_default_timeout(monkeypatch, sent):
    monkeypatch.setenv("IMAGE_API_URL", "http://img:9002")
    ok = FakeResponse(200, {})
    sent.queue = [ok]
    assert catalog.request("GET", "image", "/v1/images", params={"a": 1}) is ok
    assert sent.calls == [("GET", "http://img:9002/v1/images", {"params": {"a": 1}, "timeout": 10})]


def test_request_keeps_a_caller_timeout(monkeypatch, sent):
    monkeypatch.setenv("IMAGE_API_URL", "http://img:9002")
    sent.queue = [FakeResponse(200, {})]
    catalog.request("GET", "image", "/x", timeout=2)
    assert sent.calls[0][2]["timeout"] == 2


def test_request_does_not_retry_an_override(monkeypatch, sent):
    monkeypatch.setenv("IMAGE_API_URL", "http://img:9002")
    sent.queue = [requests.ConnectionError("refused"), FakeResponse(200, {})]
    with pytest.raises(requests.ConnectionError):
        catalog.request("GET", "image", "/x")
    assert len(sent.calls) == 1


def test_request_does_not_retry_on_http_error_status(auth_url, fetches, sent):
    fetches.queue = [FakeResponse(200, _body(_entry("image", internal="http://old:8002")))]
    bad = FakeResponse(500, {})
    sent.queue = [bad]
    assert catalog.request("GET", "image", "/x") is bad
    assert len(sent.calls) == 1


def test_request_retries_once_when_the_catalog_url_changed(auth_url, fetches, sent):
    fetches.queue = [
        FakeResponse(200, _body(_entry("image", internal="http://old:8002"))),
        FakeResponse(200, _body(_entry("image", internal="http://new:8002"))),
    ]
    ok = FakeResponse(200, {})
    sent.queue = [requests.ConnectionError("gone"), ok]
    assert catalog.request("POST", "image", "/x", json={"k": 1}) is ok
    assert [c[1] for c in sent.calls] == ["http://old:8002/x", "http://new:8002/x"]
    assert sent.calls[1][2]["json"] == {"k": 1}
    assert len(fetches.calls) == 2


def test_request_retries_only_once(auth_url, fetches, sent):
    fetches.queue = [
        FakeResponse(200, _body(_entry("image", internal="http://old:8002"))),
        FakeResponse(200, _body(_entry("image", internal="http://new:8002"))),
    ]
    sent.queue = [requests.ConnectionError("gone"), requests.ConnectionError("still gone")]
    with pytest.raises(requests.ConnectionError, match="still gone"):
        catalog.request("GET", "image", "/x")
    assert len(sent.calls) == 2


def test_request_does_not_retry_when_the_catalog_url_is_unchanged(auth_url, fetches, sent):
    fetches.queue = [FakeResponse(200, _body(_entry("image", internal="http://old:8002")))]
    first = requests.ConnectionError("gone")
    sent.queue = [first, FakeResponse(200, {})]
    with pytest.raises(requests.ConnectionError) as exc:
        catalog.request("GET", "image", "/x")
    assert exc.value is first
    assert len(sent.calls) == 1
    assert len(fetches.calls) == 2


GOOD = _entry("image", internal="http://cat:8002")
MALFORMED_ENTRIES = {
    "endpoints-none": {"name": "image", "endpoints": None},
    "endpoint-is-a-str": {"name": "image", "endpoints": ["x"]},
    "url-not-a-str": {"name": "image", "endpoints": [{"interface": "internal", "url": 5}]},
    "url-empty": {"name": "image", "endpoints": [{"interface": "internal", "url": ""}]},
    "missing-interface": {"name": "image", "endpoints": [{"url": "http://cat:8002"}]},
    "entry-not-a-dict": "image",
    "missing-name": {"endpoints": [{"interface": "internal", "url": "http://cat:8002"}]},
}


@pytest.mark.parametrize("bad", MALFORMED_ENTRIES.values(), ids=MALFORMED_ENTRIES.keys())
def test_malformed_catalog_without_cache_raises_catalog_error_naming_the_service(auth_url, fetches, bad):
    fetches.queue = [FakeResponse(200, _body(GOOD, bad))]
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.url("image")
    assert "image" in str(exc.value)


@pytest.mark.parametrize("bad", MALFORMED_ENTRIES.values(), ids=MALFORMED_ENTRIES.keys())
def test_malformed_catalog_is_never_cached_and_the_stale_good_one_is_served(auth_url, clock, fetches, bad):
    fetches.queue = [FakeResponse(200, _body(GOOD)), FakeResponse(200, _body(bad))]
    assert catalog.url("image") == "http://cat:8002"
    clock[0] += 3600
    assert catalog.url("image") == "http://cat:8002"
    clock[0] += 6  # past the retry backoff the malformed catalog is fetched (and refused) again
    assert catalog.url("image") == "http://cat:8002"
    assert len(fetches.calls) == 3


def test_request_connection_error_with_auth_down_raises_the_original_and_keeps_the_cache(auth_url, fetches, sent):
    fetches.queue = [FakeResponse(200, _body(GOOD)), requests.ConnectionError("auth down")]
    first = requests.ConnectionError("gone")
    sent.queue = [first]
    with pytest.raises(requests.ConnectionError) as exc:
        catalog.request("GET", "image", "/x")
    assert exc.value is first
    assert len(sent.calls) == 1
    assert catalog.url("image") == "http://cat:8002"


def _stale_cache_with_auth_down(fetches, clock):
    """Caches the good catalog, lets it go stale, then makes one failing fetch (fetches.calls == 2)."""
    fetches.queue = [FakeResponse(200, _body(GOOD)), requests.ConnectionError("auth down")]
    assert catalog.url("image") == "http://cat:8002"
    clock[0] += 3600
    assert catalog.url("image") == "http://cat:8002"
    assert len(fetches.calls) == 2


def test_failed_fetch_backs_off_while_serving_the_stale_cache(auth_url, clock, fetches):
    _stale_cache_with_auth_down(fetches, clock)
    clock[0] += 4
    assert catalog.url("image") == "http://cat:8002"
    assert catalog.url("image", "internal") == "http://cat:8002"
    assert len(fetches.calls) == 2
    clock[0] += 2  # 6 s after the failure: the backoff (5 s) is over
    assert catalog.url("image") == "http://cat:8002"
    assert len(fetches.calls) == 3
    clock[0] += 4
    catalog.url("image")
    assert len(fetches.calls) == 3  # the second failure started a new backoff window


def test_a_successful_fetch_clears_the_backoff(auth_url, clock, fetches):
    new = FakeResponse(200, _body(_entry("image", internal="http://new:8002")))
    fetches.queue = [FakeResponse(200, _body(GOOD)), requests.ConnectionError("auth down"), new,
                     requests.ConnectionError("down again")]
    catalog.url("image")
    clock[0] += 3600
    assert catalog.url("image") == "http://cat:8002"
    clock[0] += 6
    assert catalog.url("image") == "http://new:8002"
    assert len(fetches.calls) == 3
    clock[0] += 61  # ttl expiry right after the recovery fetches normally
    assert catalog.url("image") == "http://new:8002"
    assert len(fetches.calls) == 4
    clock[0] += 1  # and the failure of that fetch starts a fresh window, not a leftover one
    catalog.url("image")
    assert len(fetches.calls) == 4


def test_cold_failure_backs_off_and_each_service_gets_its_own_error(auth_url, clock, fetches):
    fetches.queue = [requests.ConnectionError("auth blackholed")]
    with pytest.raises(catalog.CatalogError) as first:
        catalog.url("image")
    assert "image" in str(first.value) and "auth blackholed" in str(first.value)
    assert len(fetches.calls) == 1

    clock[0] += 4
    with pytest.raises(catalog.CatalogError) as second:
        catalog.url("network")
    assert "network" in str(second.value) and "image" not in str(second.value)
    assert "auth blackholed" in str(second.value)
    assert len(fetches.calls) == 1  # no new fetch inside the backoff

    clock[0] += 2
    with pytest.raises(catalog.CatalogError):
        catalog.url("image")
    assert len(fetches.calls) == 2  # retried once the backoff is over

    fetches.queue = [FakeResponse(200, _body(GOOD))]
    clock[0] += 6
    assert catalog.url("image") == "http://cat:8002"
    assert len(fetches.calls) == 3


def test_invalidate_clears_an_active_backoff(auth_url, clock, fetches):
    _stale_cache_with_auth_down(fetches, clock)
    catalog.invalidate()
    fetches.queue = [FakeResponse(200, _body(_entry("image", internal="http://new:8002")))]
    assert catalog.url("image") == "http://new:8002"
    assert len(fetches.calls) == 3


def test_invalidate_clears_a_cold_backoff(auth_url, clock, fetches):
    fetches.queue = [requests.ConnectionError("auth down")]
    with pytest.raises(catalog.CatalogError):
        catalog.url("image")
    catalog.invalidate()
    fetches.queue = [FakeResponse(200, _body(GOOD))]
    assert catalog.url("image") == "http://cat:8002"
    assert len(fetches.calls) == 2


def test_request_retry_does_not_bypass_an_active_backoff(auth_url, clock, fetches, sent):
    _stale_cache_with_auth_down(fetches, clock)
    first = requests.ConnectionError("gone")
    sent.queue = [first, FakeResponse(200, {})]
    with pytest.raises(requests.ConnectionError) as exc:
        catalog.request("GET", "image", "/x")
    assert exc.value is first
    assert [c[1] for c in sent.calls] == ["http://cat:8002/x"]
    assert len(fetches.calls) == 2  # _expire made the entry stale but the backoff held the fetch back


def test_request_retry_refetches_once_the_backoff_is_over(auth_url, clock, fetches, sent):
    _stale_cache_with_auth_down(fetches, clock)
    fetches.queue = [
        FakeResponse(200, _body(_entry("image", internal="http://new:8002"))),
        FakeResponse(200, _body(_entry("image", internal="http://newer:8002"))),
    ]
    clock[0] += 6
    ok = FakeResponse(200, {})
    sent.queue = [requests.ConnectionError("gone"), ok]
    assert catalog.request("GET", "image", "/x") is ok
    assert [c[1] for c in sent.calls] == ["http://new:8002/x", "http://newer:8002/x"]
    assert len(fetches.calls) == 4


@pytest.mark.parametrize("bad", ["abc", "-1", "nan", "inf", ""])
def test_invalid_backoff_falls_back_to_the_default(monkeypatch, auth_url, clock, fetches, bad):
    monkeypatch.setenv("CATALOG_RETRY_BACKOFF", bad)
    _stale_cache_with_auth_down(fetches, clock)
    clock[0] += 4.9
    catalog.url("image")
    assert len(fetches.calls) == 2
    clock[0] += 0.2
    catalog.url("image")
    assert len(fetches.calls) == 3


def test_backoff_seconds_come_from_the_environment(monkeypatch, auth_url, clock, fetches):
    monkeypatch.setenv("CATALOG_RETRY_BACKOFF", "2")
    _stale_cache_with_auth_down(fetches, clock)
    clock[0] += 1.9
    catalog.url("image")
    assert len(fetches.calls) == 2
    clock[0] += 0.2
    catalog.url("image")
    assert len(fetches.calls) == 3


def test_backoff_of_zero_disables_it(monkeypatch, auth_url, clock, fetches):
    monkeypatch.setenv("CATALOG_RETRY_BACKOFF", "0")
    _stale_cache_with_auth_down(fetches, clock)
    catalog.url("image")
    catalog.url("image")
    assert len(fetches.calls) == 4
    fetches.queue = [requests.ConnectionError("auth down")]
    for _ in range(2):
        catalog.invalidate()
        with pytest.raises(catalog.CatalogError):
            catalog.url("image")
    assert len(fetches.calls) == 6
    with pytest.raises(catalog.CatalogError):  # cold, nothing cached, still no negative cache
        catalog.url("image")
    assert len(fetches.calls) == 7


def test_override_ignores_the_backoff_and_never_fetches(monkeypatch, auth_url, clock, fetches):
    fetches.queue = [requests.ConnectionError("auth down")]
    with pytest.raises(catalog.CatalogError):
        catalog.url("network")
    assert len(fetches.calls) == 1
    monkeypatch.setenv("IMAGE_API_URL", "http://img:9002")
    assert catalog.url("image") == "http://img:9002"
    assert catalog.url("identity") == "http://auth:8005"
    assert len(fetches.calls) == 1


def test_a_malformed_catalog_starts_the_backoff(auth_url, clock, fetches):
    fetches.queue = [FakeResponse(200, _body(GOOD, {"name": "image", "endpoints": None}))]
    for _ in range(3):
        with pytest.raises(catalog.CatalogError, match="malformed"):
            catalog.url("image")
    assert len(fetches.calls) == 1
    clock[0] += 6
    with pytest.raises(catalog.CatalogError, match="malformed"):
        catalog.url("image")
    assert len(fetches.calls) == 2


def test_stale_warning_is_logged_once_per_backoff_window(auth_url, clock, fetches, caplog):
    def warnings():
        return [r for r in caplog.records if r.name == "cloudx.catalog" and r.levelno == logging.WARNING]

    with caplog.at_level(logging.WARNING, logger="cloudx.catalog"):
        _stale_cache_with_auth_down(fetches, clock)
        for _ in range(5):
            clock[0] += 0.5
            catalog.url("image")
        assert len(warnings()) == 1
        clock[0] += 6
        catalog.url("image")
        catalog.url("image")
        assert len(warnings()) == 2
