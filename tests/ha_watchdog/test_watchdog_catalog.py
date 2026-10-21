"""ha-watchdog fences when ha-api stays silent. A catalog that cannot name ha-api must be exactly as silent as an
unreachable ha-api: same verdicts, same fences, never a crash and never a different outcome."""
import importlib

import pytest
import requests
from shared import catalog


class Resp:
    def __init__(self, code=200, data=None):
        self.status_code, self._d = code, data or {}

    def json(self):
        return self._d


def _down(service, interface="internal"):
    raise catalog.CatalogError(f"cannot resolve service '{service}': auth is unreachable (Connection refused [Errno 111])")


REAL_URL = catalog.url


@pytest.fixture
def make(monkeypatch, tmp_path):
    """Factory of freshly loaded watchdogs on their own state dir; ha-api answers until `silence(mode)` is called."""
    count = []

    def build():
        count.append(1)
        monkeypatch.setattr(catalog, "url", REAL_URL)
        monkeypatch.setenv("HA_WATCHDOG_STATE_DIR", str(tmp_path / f"state{len(count)}"))
        monkeypatch.setenv("HA_WATCHDOG_HOST", "h1")
        monkeypatch.setenv("HA_WATCHDOG_PEERS", "http://peer:8021")
        monkeypatch.setenv("INTERNAL_TOKEN", "tok")
        monkeypatch.setenv("HA_API_URL", "http://ha:8020")
        import services.ha_watchdog.app.main as m

        m = importlib.reload(m)
        m.calls, m.peers = [], [True]

        def request(method, url, **kw):
            if url.endswith("/internal/protected-domains/h1"):
                return Resp(200, {"domains": ["dom1"]})
            if "/internal/hosts/" in url:
                return Resp(200, {"state": "up"})
            return Resp(200)

        class R:
            returncode = 0

        monkeypatch.setattr(m.requests, "request", request)
        monkeypatch.setattr(m, "ask_peers", lambda: m.peers)
        monkeypatch.setattr(m, "run", lambda cmd, check=True: m.calls.append((cmd[-2:], m.FLAG.exists())) or R())

        def silence(mode):
            if mode == "catalog":
                monkeypatch.delenv("HA_API_URL")
                monkeypatch.setattr(catalog, "url", _down)
            else:
                def boom(*a, **k):
                    raise requests.Timeout("t")
                monkeypatch.setattr(m.requests, "request", boom)

        m.silence = silence
        return m

    return build


def _trace(m, mode, peers, times=(44, 46, 60)):
    """Run the isolation scenario and return what an observer sees after each pass."""
    st = m.State(0)
    m.step(st, 0)  # ha-api answers: the protected list is cached
    m.silence(mode)
    m.peers[:] = peers
    seen = []
    for now in times:
        m.step(st, now)
        seen.append((list(m.calls), m.FLAG.exists(), st.killed, st.last_ok))
    return seen


@pytest.mark.parametrize("peers", [[], [True], [False], [False, True]])
def test_a_catalog_failure_gives_the_same_outcome_as_a_silent_ha_api(make, peers):
    by_catalog = _trace(make(), "catalog", peers)
    by_timeout = _trace(make(), "timeout", peers)
    assert by_catalog == by_timeout


def test_catalog_failure_fences_after_the_timeout_flag_first_and_only_once(make):
    seen = _trace(make(), "catalog", [])
    assert seen[0][0] == []  # 44 s: still within the timeout
    assert seen[1][0] == [(["destroy", "dom1"], True)] and seen[1][2] is True  # flag existed when destroy ran
    assert seen[2][0] == seen[1][0]  # never twice


def test_catalog_failure_holds_when_every_peer_lost_ha_api_too(make):
    seen = _trace(make(), "catalog", [False])
    assert all(calls == [] and not flag and not killed for calls, flag, killed, _ in seen)


def test_catalog_failure_never_refreshes_last_ok(make):
    seen = _trace(make(), "catalog", [False])
    assert {last_ok for *_, last_ok in seen} == {0}


def test_no_cache_means_no_destroy_on_a_catalog_failure(make):
    w = make()
    st = w.State(0)
    w.silence("catalog")
    w.step(st, 50)  # no protected list cached yet
    assert w.calls == [] and not w.FLAG.exists()


def test_fetch_treats_a_catalog_error_as_no_answer_even_if_its_text_says_refused(make, monkeypatch):
    w = make()
    w.silence("catalog")
    assert w.fetch("GET", "/x") == (False, None)  # not "reachable": the message mentions refused / 111


def test_fetch_survives_a_catalog_that_recovers(make, monkeypatch):
    w = make()
    monkeypatch.delenv("HA_API_URL")
    monkeypatch.setattr(catalog, "url", _down)
    assert w.fetch("GET", "/x") == (False, None)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://ha-cat:8020/")
    seen = []
    monkeypatch.setattr(w.requests, "request", lambda method, url, **kw: seen.append(url) or Resp(200))
    assert w.fetch("GET", "/x")[0] is True
    assert seen == ["http://ha-cat:8020/x"]


def test_the_comma_list_override_is_split_by_the_existing_code(make, monkeypatch):
    w = make()
    monkeypatch.setenv("HA_API_URL", "http://a:1, http://b:2/")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = []

    def req(method, url, **kw):
        seen.append(url)
        raise requests.Timeout("t")

    monkeypatch.setattr(w.requests, "request", req)
    assert w.fetch("GET", "/x") == (False, None)
    assert seen == ["http://a:1/x", "http://b:2/x"]


def test_step_does_not_raise_on_a_catalog_failure(make):
    w = make()
    w.silence("catalog")
    st = w.State(0)
    w.step(st, 1)  # resolution failure is handled inside fetch, not left to the main loop's catch-all
    assert st.last_ok == 0 and not st.killed
