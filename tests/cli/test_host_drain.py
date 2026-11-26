import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import host as host_cmd  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402

C = "http://compute.test:8003"
V = "http://volume.test:8004"


def _server(sid, name, status="ACTIVE", host="n1"):
    return {"id": sid, "name": name, "status": status, "host": host, "root_volume_id": f"root-{sid}"}


def _vol(vid, host=None, backend="ceph", server=None):
    return {"id": vid, "name": f"vol-{vid}", "host": host, "backend": backend, "attached_to_server_id": server}


# --- plan_drain (pure) ------------------------------------------------------------------

def _plan(servers, volumes=()):
    return {p["server"]: p for p in host_cmd.plan_drain("n1", servers, list(volumes))}


def test_plan_drain_active_without_a_local_volume_live_migrates():
    plan = _plan([_server("s1", "a")], [_vol("root-s1", host="n9", backend="lvm", server="s1")])
    assert plan["a"]["action"] == "live-migrate"
    assert plan["a"]["id"] == "s1"
    assert set(plan["a"]) == {"server", "id", "action", "reason"}


def test_plan_drain_active_with_a_local_root_volume_skips_and_names_it():
    plan = _plan([_server("s1", "a")], [_vol("root-s1", host="n1", backend="lvm", server="s1")])
    assert plan["a"]["action"] == "skip"
    assert "root-s1" in plan["a"]["reason"] or "vol-root-s1" in plan["a"]["reason"]


def test_plan_drain_active_with_a_local_data_volume_skips_and_names_it():
    plan = _plan([_server("s1", "a")], [_vol("root-s1", backend="ceph", server="s1"),
                                         _vol("data1", host="n1", backend="lvm", server="s1")])
    assert plan["a"]["action"] == "skip"
    assert "data1" in plan["a"]["reason"] or "vol-data1" in plan["a"]["reason"]


def test_plan_drain_rbd_volumes_do_not_hold_a_server_back():
    plan = _plan([_server("s1", "a")], [_vol("root-s1", host=None, backend="ceph", server="s1"),
                                         _vol("data1", host=None, backend="ceph", server="s1")])
    assert plan["a"]["action"] == "live-migrate"


def test_plan_drain_a_local_volume_of_another_server_or_node_is_ignored():
    plan = _plan([_server("s1", "a")], [_vol("other", host="n1", backend="lvm", server="s2"),
                                         _vol("data1", host="n2", backend="lvm", server="s1")])
    assert plan["a"]["action"] == "live-migrate"


def test_plan_drain_shutoff_cold_migrates():
    plan = _plan([_server("s1", "a", status="SHUTOFF")], [_vol("root-s1", host="n1", backend="lvm", server="s1")])
    assert plan["a"]["action"] == "cold-migrate"


@pytest.mark.parametrize("status", ["PAUSED", "ERROR", "MIGRATING"])
def test_plan_drain_other_statuses_skip_and_name_the_status(status):
    plan = _plan([_server("s1", "a", status=status)])
    assert plan["a"]["action"] == "skip"
    assert status in plan["a"]["reason"]


def test_plan_drain_servers_on_other_nodes_do_not_appear():
    plan = _plan([_server("s1", "a"), _server("s2", "b", host="n2"), _server("s3", "c", host=None)])
    assert list(plan) == ["a"]


# --- the commands -----------------------------------------------------------------------

class Fake:
    """A pretend compute + block-store API. `after` maps a server id to the (status, host) series that GET
    /servers/{id} returns on successive polls (the last one repeats)."""

    def __init__(self, servers, volumes=(), after=None, refuse=(), no_answer=()):
        self.servers, self.volumes = servers, list(volumes)
        self.after = {k: list(v) for k, v in (after or {}).items()}
        self.refuse = set(refuse)
        self.no_answer = set(no_answer)   # the migrate POST of these gets no answer (a requests-level error)
        self.calls = []
        self.sleeps = []

    def __call__(self, method, url, profile="default", **kw):
        self.calls.append((method, url.replace(C, "C").replace(V, "V"), kw.get("json")))
        if method == "get" and url == f"{C}/servers?limit=1000":
            if isinstance(self.servers, BaseException):
                raise self.servers
            return {"items": self.servers}
        if method == "get" and url == f"{V}/volumes":
            return {"items": self.volumes}
        if method == "get" and url.startswith(f"{C}/servers/"):
            sid = url.rsplit("/", 1)[1]
            series = self.after[sid]
            item = series.pop(0) if len(series) > 1 else series[0]
            if isinstance(item, BaseException):  # a failed poll: SystemExit from _api, or a requests error
                raise item
            status, host = item
            return {"id": sid, "status": status, "host": host}
        if method == "post" and url.rsplit("/", 2)[-2] in self.refuse:
            raise SystemExit(1)
        if method == "post" and url.rsplit("/", 2)[-2] in self.no_answer:
            raise requests.ReadTimeout("read timed out")
        return {"ok": True}


@pytest.fixture
def install(monkeypatch):
    def _install(fake):
        monkeypatch.setattr(host_cmd, "_api", fake)
        monkeypatch.setattr(host_cmd, "DEFAULT_URLS", {"compute": C, "volume": V})
        monkeypatch.setattr(host_cmd.time, "sleep", lambda s: fake.sleeps.append(s))
        return fake
    return _install


def _drain(*args):
    return CliRunner().invoke(cli, ["host", "drain", "n1", *args])


DISABLE = ("put", "C/os-services/n1", {"disabled": True, "disabled_reason": "drain"})


def test_drain_disables_the_node_then_migrates_one_by_one_and_waits(install):
    fake = install(Fake([_server("s1", "a"), _server("s2", "b"), _server("s9", "z", host="n2")],
                        after={"s1": [("MIGRATING", "n1"), ("MIGRATING", "n2"), ("ACTIVE", "n2")],
                               "s2": [("ACTIVE", "n3")]}))

    res = _drain()

    assert res.exit_code == 0, res.output
    assert fake.calls == [
        DISABLE,
        ("get", "C/servers?limit=1000", None),
        ("get", "V/volumes", None),
        ("post", "C/servers/s1/live-migrate", None),
        ("get", "C/servers/s1", None),
        ("get", "C/servers/s1", None),
        ("get", "C/servers/s1", None),
        ("post", "C/servers/s2/live-migrate", None),
        ("get", "C/servers/s2", None),
    ]
    assert fake.sleeps and set(fake.sleeps) == {5}
    assert "a" in res.output and "b" in res.output and "z" not in res.output


def test_drain_cold_migrates_a_shutoff_server_and_waits_for_shutoff(install):
    fake = install(Fake([_server("s1", "a", status="SHUTOFF")],
                        after={"s1": [("MIGRATING", "n2"), ("SHUTOFF", "n2")]}))

    res = _drain()

    assert res.exit_code == 0, res.output
    assert ("post", "C/servers/s1/migrate", None) in fake.calls
    assert not any(c[1].endswith("live-migrate") for c in fake.calls)


def test_drain_reports_a_migration_that_came_back_to_the_same_node_as_failed(install):
    fake = install(Fake([_server("s1", "a"), _server("s2", "b")],
                        after={"s1": [("MIGRATING", "n1"), ("ACTIVE", "n1")], "s2": [("ACTIVE", "n2")]}))

    res = _drain()

    assert res.exit_code != 0
    assert "failed" in res.output
    assert "n1" in res.output
    # the other server is still tried
    assert ("post", "C/servers/s2/live-migrate", None) in fake.calls


def test_drain_reports_an_error_status_as_failed(install):
    install(Fake([_server("s1", "a")], after={"s1": [("ERROR", "n1")]}))

    res = _drain()

    assert res.exit_code != 0
    assert "failed" in res.output and "ERROR" in res.output


def test_drain_times_out(install):
    fake = install(Fake([_server("s1", "a")], after={"s1": [("MIGRATING", "n1")]}))

    res = _drain("--timeout", "20")

    assert res.exit_code != 0
    assert "failed" in res.output and "timed out" in res.output
    assert sum(fake.sleeps) <= 25  # gave up after about 20 seconds, not never


def test_drain_a_refused_migration_is_a_failure_and_the_rest_still_run(install):
    fake = install(Fake([_server("s1", "a"), _server("s2", "b")], after={"s2": [("ACTIVE", "n2")]}, refuse={"s1"}))

    res = _drain()

    assert res.exit_code != 0
    assert "failed" in res.output
    assert ("post", "C/servers/s2/live-migrate", None) in fake.calls


def test_drain_of_an_empty_node_exits_0(install):
    fake = install(Fake([_server("s9", "z", host="n2")]))

    res = _drain()

    assert res.exit_code == 0, res.output
    assert "no servers on n1" in res.output
    assert fake.calls[0] == DISABLE  # the node is still taken out of scheduling


def test_drain_exits_nonzero_when_something_was_skipped(install):
    fake = install(Fake([_server("s1", "a"), _server("s2", "b", status="PAUSED")],
                        after={"s1": [("ACTIVE", "n2")]}))

    res = _drain()

    assert res.exit_code != 0
    assert "skipped" in res.output and "PAUSED" in res.output
    assert not any(c[1].startswith("C/servers/s2") and c[0] == "post" for c in fake.calls)


def test_drain_a_skipped_server_with_a_local_volume_names_the_volume(install):
    install(Fake([_server("s1", "a")], volumes=[_vol("root-s1", host="n1", backend="lvm", server="s1")]))

    res = _drain()

    assert res.exit_code != 0
    assert "root-s1" in res.output


def test_drain_failure_leaves_the_node_disabled_and_says_how_to_enable_it(install):
    fake = install(Fake([_server("s1", "a")], after={"s1": [("ERROR", "n1")]}))

    res = _drain()

    assert res.exit_code != 0
    assert "cloudx host enable n1" in res.output
    assert not any(c[0] == "put" and c[2] == {"disabled": False} for c in fake.calls)
    assert [c for c in fake.calls if c[0] == "put"] == [DISABLE]


def test_drain_success_also_says_the_node_stays_disabled(install):
    install(Fake([_server("s1", "a")], after={"s1": [("ACTIVE", "n2")]}))

    res = _drain()

    assert res.exit_code == 0, res.output
    assert "cloudx host enable n1" in res.output


def test_drain_dry_run_prints_the_plan_and_changes_nothing(install):
    fake = install(Fake([_server("s1", "a"), _server("s2", "b", status="SHUTOFF")]))

    res = _drain("--dry-run")

    assert res.exit_code == 0, res.output
    assert "live-migrate" in res.output and "cold-migrate" in res.output
    assert not [c for c in fake.calls if c[0] in ("put", "post")]
    assert fake.sleeps == []


def test_drain_dry_run_exits_nonzero_when_the_plan_has_a_skip(install):
    fake = install(Fake([_server("s1", "a", status="PAUSED")]))

    res = _drain("--dry-run")

    assert res.exit_code != 0
    assert "skip" in res.output
    assert not [c for c in fake.calls if c[0] in ("put", "post")]


def test_enable_puts_disabled_false(install):
    fake = install(Fake([]))

    res = CliRunner().invoke(cli, ["host", "enable", "n1"])

    assert res.exit_code == 0, res.output
    assert fake.calls == [("put", "C/os-services/n1", {"disabled": False})]


class PagedFake(Fake):
    """GET /servers serves `page` servers at a time, following the API's marker (the id of the last one seen)."""

    def __init__(self, servers, page, **kw):
        super().__init__(servers, **kw)
        self.page = page

    def __call__(self, method, url, profile="default", **kw):
        if method == "get" and url.startswith(f"{C}/servers?"):
            self.calls.append((method, url.replace(C, "C"), None))
            query = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))
            assert query["limit"] == str(self.page)
            start = 0
            if "marker" in query:
                start = [s["id"] for s in self.servers].index(query["marker"]) + 1
            return {"items": self.servers[start:start + self.page]}
        return super().__call__(method, url, profile, **kw)


def test_drain_follows_the_server_list_pages(install, monkeypatch):
    monkeypatch.setattr(host_cmd, "SERVER_PAGE", 2)
    servers = [_server("s1", "a", host="n2"), _server("s2", "b", host="n2"), _server("s3", "c"), _server("s4", "d")]
    fake = install(PagedFake(servers, 2, after={"s3": [("ACTIVE", "n2")], "s4": [("ACTIVE", "n3")]}))

    res = _drain()

    assert res.exit_code == 0, res.output
    lists = [c[1] for c in fake.calls if c[1].startswith("C/servers?")]
    # the third page is empty: a full page always asks for the next one
    assert lists == ["C/servers?limit=2", "C/servers?limit=2&marker=s2", "C/servers?limit=2&marker=s4"]
    assert ("post", "C/servers/s3/live-migrate", None) in fake.calls
    assert ("post", "C/servers/s4/live-migrate", None) in fake.calls


def test_a_short_page_ends_the_listing(install, monkeypatch):
    monkeypatch.setattr(host_cmd, "SERVER_PAGE", 2)
    fake = install(PagedFake([_server("s1", "a", host="n2"), _server("s2", "b", host="n2"), _server("s3", "c", host="n2")], 2))

    res = _drain("--dry-run")

    assert res.exit_code == 0, res.output
    assert [c[1] for c in fake.calls if c[1].startswith("C/servers?")] == ["C/servers?limit=2", "C/servers?limit=2&marker=s2"]


# --- follow-ups: a failed poll is retried, an unknown status stops the drain (C1); the enable hint (C2) -------

def test_a_few_failed_polls_are_retried_and_the_drain_goes_on(install):
    fake = install(Fake([_server("s1", "a"), _server("s2", "b")],
                        after={"s1": [SystemExit(1), requests.Timeout("read timed out"), ("ACTIVE", "n2")],
                               "s2": [("ACTIVE", "n3")]}))

    res = _drain()

    assert res.exit_code == 0, res.output
    assert ("post", "C/servers/s2/live-migrate", None) in fake.calls
    assert "Traceback" not in res.output


@pytest.mark.parametrize("error", [SystemExit(1), requests.Timeout("read timed out")], ids=["api error", "timeout"])
def test_an_unknown_status_stops_the_drain_before_the_next_migration(install, error):
    fake = install(Fake([_server("s1", "a"), _server("s2", "b")], after={"s1": [error], "s2": [("ACTIVE", "n3")]}))

    res = _drain()

    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit), res.exception   # no traceback
    assert [c for c in fake.calls if c[1] == "C/servers/s1"] == [("get", "C/servers/s1", None)] * host_cmd.POLL_FAILURES
    assert not any(c[0] == "post" and "/s2/" in c[1] for c in fake.calls)   # no second migration on top of the first
    assert "drain stopped: the state of a is unknown" in res.output
    assert "not started" in res.output                                     # b is listed, not migrated
    assert "cloudx host enable n1" in res.output


@pytest.mark.parametrize("error", [SystemExit(1), requests.Timeout("read timed out")], ids=["api error", "timeout"])
def test_a_failed_listing_after_the_disable_still_says_how_to_enable(install, error):
    fake = install(Fake(error))

    res = _drain()

    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit), res.exception
    assert fake.calls[0] == DISABLE
    assert "cloudx host enable n1" in res.output


def test_a_migrate_request_without_an_answer_stops_the_drain(install):
    # the request may have reached compute-api: the migration may be running, so no second one is started
    fake = install(Fake([_server("s1", "a"), _server("s2", "b")], after={"s2": [("ACTIVE", "n3")]}, no_answer={"s1"}))

    res = _drain()

    assert res.exit_code == 1
    assert res.exception is None or isinstance(res.exception, SystemExit), res.exception
    assert not any(c[0] == "post" and "/s2/" in c[1] for c in fake.calls)
    assert not any(c[1] == "C/servers/s1" for c in fake.calls)              # no polling of an unknown request
    assert "drain stopped: the state of a is unknown" in res.output and "no answer" in res.output
    assert "cloudx host enable n1" in res.output
