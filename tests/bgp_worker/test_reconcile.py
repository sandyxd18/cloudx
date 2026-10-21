from __future__ import annotations

import logging
import os
import stat
from types import SimpleNamespace

import pytest

from services.bgp_worker.app import main as w

SP = {
    "id": "bgpsp-abcdef123456", "router_id": "r1", "short": "abcdef12", "outer_lsp": "lsp-bgp-abcdef12-out",
    "outer_mac": "fa:16:3e:00:00:01", "outer_ip": "172.24.4.50", "outer_prefixlen": 24, "outer_gateway_ip": "172.24.4.1",
    "router_ext_ip": "172.24.4.1", "local_subnets": ["10.0.0.0/24"],
    "peers": [{"id": "p1", "name": "a", "peer_ip": "172.24.4.60", "remote_as": 65001, "local_as": 65000,
               "auth_password": "s3cr3tpw", "advertised_prefixes": ["10.0.0.0/24"]}],
}
NS, DEV = "qbgp-abcdef12", "bgpabcdef12-out"
PROTOS = "Name Proto Table State Since Info\nbgp_p1 BGP --- up 23:56 Established\n"
ROUTES = "Table master4:\n10.202.0.0/24 unicast [bgp_p1 23:56] * (100) [AS65001i]\n\tvia 172.24.4.60 on x\n"


class Env:
    def __init__(self, mp, tmp):
        self.cmds, self.posts, self.speakers = [], [], [SP]
        self.protos, self.routes, self.netns = PROTOS, ROUTES, ""
        self.root = tmp / "bgp"
        mp.setenv("NETWORKING_API_URL", "http://networking.test:8001")
        w.ROOT, w.HOST = self.root, "h1"
        w._applied.clear(); w._reported.clear()
        mp.setattr(w, "run", self.run)
        mp.setattr(w.time, "sleep", lambda s: None)
        mp.setattr(w.requests, "get", lambda *a, **k: SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"speakers": self.speakers}))
        mp.setattr(w.requests, "post", lambda url, json=None, **k: self.posts.append(json) or SimpleNamespace(raise_for_status=lambda: None))

    def run(self, cmd, **k):
        self.cmds.append(list(cmd))
        out = ""
        if cmd[0] == "sh":  # detached bird start: simulate pid + ctl
            d = self.root / SP["id"]
            (d / "bird.pid").write_text("4242"); (d / "bird.ctl").write_text("")
        elif cmd[:2] == ["ip", "netns"] and cmd[2] == "list":
            out = self.netns
        elif "show" in cmd and "protocols" in cmd:
            out = self.protos
        elif "show" in cmd and "route" in cmd:
            out = self.routes
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    def has(self, *argv):
        return list(argv) in self.cmds

    def configures(self):
        return [c for c in self.cmds if c[-1] == "configure"]


@pytest.fixture
def env(monkeypatch, tmp_path):
    return Env(monkeypatch, tmp_path)


def test_parse_routes():
    assert w.parse_routes(ROUTES) == [{"prefix": "10.202.0.0/24", "nexthop": "172.24.4.60"}]
    assert w.parse_routes("BIRD 2.14 ready.\nTable master4:\n") == []
    two = ROUTES + "10.203.0.0/24 unicast [bgp_p1 23:56] * (100) [AS65001i]\n\tvia 172.24.4.61 on x\n"
    assert [r["prefix"] for r in w.parse_routes(two)] == ["10.202.0.0/24", "10.203.0.0/24"]
    assert w.parse_routes("10.9.0.0/24 blackhole [static4 23:56] * (200)\n") == []


def test_first_tick_builds_netns_port_and_starts_bird(env):
    w.tick()
    d = env.root / SP["id"]
    ctl = f"{d}/bird.ctl"
    assert env.has("ip", "netns", "add", NS)
    assert env.has("ovs-vsctl", "--may-exist", "add-port", "br-int", DEV, "--", "set", "Interface", DEV, "type=internal",
                   f"external_ids:iface-id={SP['outer_lsp']}", f"external_ids:cloudx_bgp={SP['id']}")
    x = ["ip", "netns", "exec", NS, "ip"]
    assert env.has(*x, "link", "set", DEV, "address", SP["outer_mac"])
    assert env.has(*x, "addr", "replace", "172.24.4.50/24", "dev", DEV)
    assert env.has(*x, "route", "replace", "default", "via", "172.24.4.1", "dev", DEV)
    assert env.has(*x, "link", "set", "lo", "up")
    start = [c for c in env.cmds if c[0] == "sh"]
    assert len(start) == 1 and f"ip netns exec {NS} " in start[0][2]
    assert f"-c {d / 'bird.conf'} -s {ctl} -P {d}/bird.pid" in start[0][2]
    assert env.configures() == []  # fresh bird already read this conf
    conf = d / "bird.conf"
    from shared.bird_cfg import render
    assert conf.read_text() == render({"outer_ip": "172.24.4.50", "router_ext_ip": "172.24.4.1", "local_subnets": ["10.0.0.0/24"]},
                                      SP["peers"], mask_password=False)
    if os.name != "nt":
        assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert env.posts == [{"peers": {"p1": {"status": "established", "status_reason": None,
                                           "routes": [{"prefix": "10.202.0.0/24", "nexthop": "172.24.4.60"}]}}}]


def test_steady_state_and_password_change(env):
    w.tick()
    env.cmds.clear(); env.posts.clear()
    w.tick()
    assert env.configures() == [] and env.posts == []  # no change: no configure, no status post
    assert not [c for c in env.cmds if c[0] == "sh"]  # bird not restarted
    import copy
    sp2 = copy.deepcopy(SP); sp2["peers"][0]["auth_password"] = "other-pw-1"
    env.speakers = [sp2]
    w.tick()
    ctl = f"{env.root / SP['id']}/bird.ctl"
    assert env.configures() == [["birdc", "-s", ctl, "configure"]]


def test_dead_bird_is_restarted(env, monkeypatch):
    w.tick()
    env.cmds.clear()
    monkeypatch.setattr(w, "pid_of", lambda d: None)
    w.tick()
    assert len([c for c in env.cmds if c[0] == "sh"]) == 1


def test_orphan_cleanup(env, monkeypatch):
    od = env.root / "bgpsp-deadbeef0000"
    od.mkdir(parents=True)
    (od / "bird.pid").write_text("999")
    (od / "bird.conf").write_text("x")
    env.netns = "qbgp-deadbeef (id: 3)\n"
    env.speakers = []
    monkeypatch.setattr(w, "pid_of", lambda d: 999 if d == od else None)
    w.tick()
    assert env.has("kill", "999")
    assert env.has("ovs-vsctl", "--if-exists", "del-port", "br-int", "bgpdeadbeef-out")
    assert env.has("ip", "netns", "del", "qbgp-deadbeef")
    assert not od.exists()


def test_status_only_on_change_and_routes_count(env):
    w.tick()
    env.posts.clear()
    env.routes = ROUTES + "10.203.0.0/24 unicast [bgp_p1 23:56] * (100) [AS65001i]\n\tvia 172.24.4.61 on x\n"
    w.tick()
    assert len(env.posts) == 1 and len(env.posts[0]["peers"]["p1"]["routes"]) == 2
    env.protos = "bgp_p1 BGP --- start 23:56 Active\n"
    w.tick()
    assert env.posts[-1]["peers"]["p1"]["status"] == "down" and env.posts[-1]["peers"]["p1"]["routes"] == []


def test_protocol_name_matched_exactly(env):
    env.protos = "bgp_p10 BGP --- up 23:56 Established\n"
    w.tick()
    assert env.posts[0]["peers"]["p1"]["status"] == "down"


def test_password_never_logged_or_in_argv(env, caplog):
    caplog.set_level(logging.DEBUG)
    w.tick(); w.tick()
    assert "s3cr3tpw" not in caplog.text
    assert not any("s3cr3tpw" in " ".join(c) for c in env.cmds)


def test_apply_failure_reports_error_not_down(env, monkeypatch):
    def boom(spk):
        raise RuntimeError("bird configure failed: x")
    monkeypatch.setattr(w, "apply_speaker", boom)
    w.tick()
    assert env.posts == [{"peers": {"p1": {"status": "error", "status_reason": "bird configure failed: x", "routes": []}}}]
