import importlib
import json

import pytest


class Resp:
    def __init__(self, code=200, data=None):
        self.status_code, self._d = code, data or {}

    def json(self):
        return self._d


@pytest.fixture
def w(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_WATCHDOG_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("HA_WATCHDOG_HOST", "h1")
    monkeypatch.setenv("HA_WATCHDOG_PEERS", "http://peer:8021")
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    import services.ha_watchdog.app.main as m
    m = importlib.reload(m)
    m.calls, m.net = [], {"up": True, "state": "up", "peers": [True]}
    m.busy = set()  # domains that have a live-migration job running
    m.migrating = lambda domain: domain in m.busy

    def fetch(method, path):
        if not m.net["up"]:
            return False, None
        if path.startswith("/internal/protected-domains"):
            return True, Resp(200, {"domains": ["dom1"]})
        if path.startswith("/internal/hosts"):
            return True, Resp(200, {"state": m.net["state"]})
        return True, Resp(200)

    class R:
        returncode = 0

    def run(cmd, check=True):
        m.calls.append((cmd[-2:], m.FLAG.exists()))
        return R()

    monkeypatch.setattr(m, "fetch", fetch)
    monkeypatch.setattr(m, "run", run)
    monkeypatch.setattr(m, "ask_peers", lambda: m.net["peers"])
    return m


def isolate(w, st):
    w.step(st, 0)  # caches the protected list
    w.net["up"] = False


def test_kills_after_timeout_and_writes_flag_first(w):
    st = w.State(0)
    isolate(w, st)
    w.step(st, 44)
    assert w.calls == []
    w.step(st, 46)
    assert w.calls == [(["destroy", "dom1"], True)]  # flag existed when destroy ran
    w.step(st, 60)
    assert len(w.calls) == 1  # never twice


def test_holds_when_every_peer_lost_ha_api(w):
    st = w.State(0)
    isolate(w, st)
    w.net["peers"] = [False]
    w.step(st, 50)
    assert w.calls == [] and not w.FLAG.exists()


def test_kills_when_no_peer_answers_but_holds_without_peers(w):
    st = w.State(0)
    isolate(w, st)
    w.net["peers"] = []
    w.step(st, 50)
    assert len(w.calls) == 1
    w.PEERS.clear()
    st2 = w.State(0)
    w.step(st2, 50)
    assert len(w.calls) == 1


def test_empty_cache_and_dry_run_do_not_destroy(w):
    st = w.State(0)
    w.net["up"] = False
    w.step(st, 50)  # no cache yet
    assert w.calls == []
    w.net["up"] = True
    w.step(st, 51)
    w.net["up"] = False
    w.DRY_RUN = True
    w.step(st, 100)
    assert w.calls == [] and not w.FLAG.exists()


def test_flag_cleared_only_when_host_is_up(w):
    w.FLAG.write_text("[]")
    w.net["state"] = "fenced"
    st = w.State(0)
    w.step(st, 1)
    assert w.FLAG.exists()
    w.net["state"] = "up"
    w.step(st, 2)
    assert not w.FLAG.exists()


def test_http_error_or_refused_counts_as_reachable(monkeypatch, w):
    import requests

    importlib.reload(w)  # real fetch
    monkeypatch.setenv("HA_API_URL", "http://ha:8020")
    monkeypatch.setattr(w.requests, "request", lambda *a, **k: Resp(503))
    assert w.fetch("GET", "/x")[0] is True

    def refused(*a, **k):
        raise requests.ConnectionError("[Errno 111] Connection refused")

    monkeypatch.setattr(w.requests, "request", refused)
    assert w.fetch("GET", "/x")[0] is True

    def timeout(*a, **k):
        raise requests.Timeout("t")

    monkeypatch.setattr(w.requests, "request", timeout)
    assert w.fetch("GET", "/x")[0] is False


def test_any_ha_api_instance_answering_counts(monkeypatch, w):
    import requests

    importlib.reload(w)
    monkeypatch.setenv("HA_API_URL", "http://a, http://b/")  # a comma list is returned verbatim by the catalog
    def req(method, url, **k):
        if url.startswith("http://a"):
            raise requests.Timeout("t")
        return Resp(200, {"domains": ["d"]})
    monkeypatch.setattr(w.requests, "request", req)
    ok, r = w.fetch("GET", "/x")
    assert ok is True and r.json() == {"domains": ["d"]}
    monkeypatch.setattr(w.requests, "request", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("t")))
    assert w.fetch("GET", "/x") == (False, None)   # all silent: unreachable
    monkeypatch.setattr(w.requests, "request", lambda m, u, **k: Resp(503) if u.startswith("http://a") else Resp(200))
    assert w.fetch("GET", "/x")[1].status_code == 200   # a healthy instance beats a 5xx one


def _reach_status(w, token):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    servers = []
    real = ThreadingHTTPServer

    class Capture(real):
        def __init__(self, addr, handler):
            super().__init__(("127.0.0.1", 0), handler)
            servers.append(self)

        def serve_forever(self, *a, **k):
            started.set()
            super().serve_forever(*a, **k)

    started = threading.Event()
    w.ThreadingHTTPServer = Capture
    t = threading.Thread(target=w.serve_reach, args=(w.State(0.0),), daemon=True)
    t.start()
    assert started.wait(5)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", servers[0].server_address[1], timeout=5)
        headers = {"X-Internal-Token": token} if token is not None else {}
        conn.putrequest("GET", "/reach")
        for k, v in headers.items():
            conn.putheader(k, v.encode("latin-1"))
        conn.endheaders()
        return conn.getresponse().status
    finally:
        servers[0].shutdown()
        servers[0].server_close()


def test_reach_answers_401_to_a_non_ascii_token_and_200_to_the_right_one(w):
    assert _reach_status(w, "caf\xe9") == 401
    assert _reach_status(w, "wrong") == 401
    assert _reach_status(w, None) == 401
    assert _reach_status(w, "tok") == 200


def test_a_migrating_domain_is_left_alone_while_its_job_runs(w):
    st = w.State(0)
    isolate(w, st)
    w.busy.add("dom1")
    w.step(st, 46)
    assert w.calls == [] and not w.FLAG.exists()
    assert st.killed is False  # it looks again on the next pass


def test_the_domain_is_destroyed_on_the_pass_after_its_job_ends(w):
    st = w.State(0)
    isolate(w, st)
    w.busy.add("dom1")
    w.step(st, 46)
    assert w.calls == []  # nothing while the job runs
    w.busy.clear()
    w.step(st, 51)
    assert w.calls == [(["destroy", "dom1"], True)]
    w.step(st, 56)
    assert len(w.calls) == 1  # never twice


def test_only_the_domain_without_a_job_is_destroyed_at_first(w):
    st = w.State(0)
    isolate(w, st)
    w.CACHE.write_text(json.dumps({"domains": ["dom1", "dom2"]}))
    w.busy.add("dom1")
    w.step(st, 46)
    assert w.calls == [(["destroy", "dom2"], True)]
    assert st.killed is False
    w.busy.clear()
    w.step(st, 51)
    assert [c[0] for c in w.calls] == [["destroy", "dom2"], ["destroy", "dom1"]]
    w.step(st, 56)
    assert len(w.calls) == 2


def test_a_job_that_outlasts_the_grace_no_longer_protects_the_domain(w):
    w.MIGRATION_GRACE = 100
    st = w.State(0)
    isolate(w, st)
    w.busy.add("dom1")
    w.step(st, 46)
    w.step(st, 100)  # 54 s since the first deferral
    assert w.calls == []
    w.step(st, 147)  # 101 s
    assert w.calls == [(["destroy", "dom1"], True)]


def test_ha_api_coming_back_resets_the_grace_clock(w):
    st = w.State(0)
    isolate(w, st)
    w.busy.add("dom1")
    w.step(st, 46)
    assert st.busy_since == 46
    w.net["up"] = True
    w.step(st, 50)
    assert st.busy_since is None


class Out:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode = stdout, returncode


@pytest.mark.parametrize("text,rc,expected", [
    ("Job type:         None\n\n", 0, False),
    ("Job type:         Unbounded\nOperation:        Outgoing migration\n", 0, True),
    ("Job type:         Bounded\nOperation:        Incoming migration\n", 0, True),
    ("Job type:         Unbounded\nOperation:        Snapshot\n", 0, False),
    ("Job type:         Unbounded\n", 0, True),  # a libvirt that names no operation: assume a migration
    ("error: Requested operation is not valid: domain is not running\n", 1, False),
    ("", 0, False),
])
def test_migrating_reads_the_job_that_virsh_reports(w, monkeypatch, text, rc, expected):
    m = importlib.reload(w)
    seen = []
    monkeypatch.setattr(m, "run", lambda cmd, check=True: seen.append(cmd) or Out(text, rc))
    assert m.migrating("dom1") is expected
    assert seen == [["virsh", "-c", m.LIBVIRT_URI, "domjobinfo", "dom1"]]
