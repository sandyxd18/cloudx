"""websockify on a compute node asks this plugin where a console token leads: the loopback VNC port, or nothing.
The token is also bound to a libvirt domain, which the plugin checks still owns that port."""
import sys
import types

import pytest

from shared import console_token
from services.compute_worker.app.console_proxy import ConsoleTokenPlugin

DOM = "instance-0001"


class FakeLibvirt:
    """A stand-in for the `libvirt` module: one connection factory over a {domain name: vnc port} table."""

    def __init__(self, domains):
        self.domains = domains
        self.opened = []
        self.closed = 0
        self.module = types.ModuleType("libvirt")
        self.module.open = self._open

    def _open(self, uri):
        self.opened.append(uri)
        return types.SimpleNamespace(lookupByName=self._lookup, close=self._close)

    def _close(self):
        self.closed += 1

    def _lookup(self, name):
        if name not in self.domains:
            raise RuntimeError(f"Domain not found: {name}")
        port = self.domains[name]
        xml = (f"<domain><devices><graphics type='spice' port='5999'/>"
               f"<graphics type='vnc' port='{port}' autoport='yes' listen='127.0.0.1'/></devices></domain>")
        return types.SimpleNamespace(XMLDesc=lambda flags: xml)


@pytest.fixture
def libvirt(monkeypatch):
    fake = FakeLibvirt({DOM: 5903})
    monkeypatch.setitem(sys.modules, "libvirt", fake.module)
    return fake


@pytest.fixture
def plugin(monkeypatch, libvirt):
    monkeypatch.setenv("COMPUTE_HOST", "cloudx-comp-01")
    return ConsoleTokenPlugin(None)


def test_lookup_returns_loopback_target(plugin):
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903, domain=DOM)

    assert plugin.lookup(token) == ("127.0.0.1", 5903)


def test_lookup_returns_none_for_invalid(plugin):
    assert plugin.lookup("garbage") is None
    assert plugin.lookup(console_token.issue_token("s1", "cloudx-comp-02", 5903, domain=DOM)) is None
    assert plugin.lookup(console_token.issue_token("s1", "cloudx-comp-01", 5903, ttl=-1, domain=DOM)) is None


def test_lookup_uses_the_same_node_name_as_the_worker(monkeypatch, libvirt):
    """The token's host is the worker's COMPUTE_HOST: COMPUTE_HOST, else NODE_NAME, else cloudx-compute-01 (config.py)."""
    monkeypatch.delenv("COMPUTE_HOST", raising=False)
    monkeypatch.delenv("NODE_NAME", raising=False)
    default_token = console_token.issue_token("s1", "cloudx-compute-01", 5903, domain=DOM)
    node_token = console_token.issue_token("s1", "edge-7", 5903, domain=DOM)

    assert ConsoleTokenPlugin(None).lookup(default_token) == ("127.0.0.1", 5903)
    monkeypatch.setenv("NODE_NAME", "edge-7")
    assert ConsoleTokenPlugin(None).lookup(node_token) == ("127.0.0.1", 5903)
    assert ConsoleTokenPlugin(None).lookup(default_token) is None
    monkeypatch.setenv("COMPUTE_HOST", "cloudx-comp-01")
    assert ConsoleTokenPlugin(None).lookup(node_token) is None


def test_lookup_returns_none_when_the_node_name_is_empty(monkeypatch, libvirt):
    monkeypatch.setenv("COMPUTE_HOST", "")
    token = console_token.issue_token("s1", "", 5903, domain=DOM)

    assert ConsoleTokenPlugin(None).lookup(token) is None


@pytest.mark.parametrize("secret", [None, ""])
def test_lookup_without_a_secret_returns_none(monkeypatch, libvirt, secret):
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903, domain=DOM)
    monkeypatch.setenv("COMPUTE_HOST", "cloudx-comp-01")
    if secret is None:
        monkeypatch.delenv("AUTH_SECRET_KEY")
    else:
        monkeypatch.setenv("AUTH_SECRET_KEY", secret)

    assert ConsoleTokenPlugin(None).lookup(token) is None


# ── the domain must still own the port ───────────────────────────────────────
def test_lookup_checks_the_domain_with_libvirt_and_closes_the_connection(plugin, libvirt, monkeypatch):
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903, domain=DOM)

    assert plugin.lookup(token) == ("127.0.0.1", 5903)
    assert libvirt.opened == ["qemu:///system"] and libvirt.closed == 1

    monkeypatch.setenv("LIBVIRT_URI", "qemu+unix:///system")   # the worker's own variable
    assert plugin.lookup(token) == ("127.0.0.1", 5903)
    assert libvirt.opened[-1] == "qemu+unix:///system"


def test_lookup_refuses_a_domain_that_moved_to_another_port(plugin, libvirt):
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903, domain=DOM)
    libvirt.domains[DOM] = 5904    # the port went back to the pool and the VM was started again elsewhere

    assert plugin.lookup(token) is None
    assert libvirt.closed == 1


def test_lookup_refuses_a_port_now_owned_by_another_domain(plugin, libvirt):
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903, domain=DOM)
    libvirt.domains.update({DOM: 5910, "instance-0002": 5903})    # another tenant's VM took port 5903

    assert plugin.lookup(token) is None


def test_lookup_refuses_a_domain_that_no_longer_exists(plugin, libvirt):
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903, domain=DOM)
    libvirt.domains.clear()

    assert plugin.lookup(token) is None
    assert libvirt.closed == 1


def test_lookup_refuses_a_stopped_domain_without_a_vnc_port(plugin, libvirt):
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903, domain=DOM)
    libvirt.domains[DOM] = -1    # a shut-off domain has port='-1'

    assert plugin.lookup(token) is None


def test_lookup_refuses_when_libvirt_cannot_be_imported(plugin, monkeypatch):
    monkeypatch.setitem(sys.modules, "libvirt", None)    # `import libvirt` raises ImportError
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903, domain=DOM)

    assert plugin.lookup(token) is None


def test_lookup_refuses_when_the_connection_fails(plugin, libvirt):
    def refuse(uri):
        raise RuntimeError("cannot connect")

    libvirt.module.open = refuse
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903, domain=DOM)

    assert plugin.lookup(token) is None


def test_lookup_refuses_unparseable_xml_and_still_closes(plugin, libvirt, monkeypatch):
    monkeypatch.setattr(libvirt, "_lookup", lambda name: types.SimpleNamespace(XMLDesc=lambda flags: "<not-xml"))
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903, domain=DOM)

    assert plugin.lookup(token) is None
    assert libvirt.closed == 1


def test_lookup_refuses_a_token_without_a_domain(plugin, libvirt):
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903)

    assert plugin.lookup(token) is None
    assert libvirt.opened == []     # rejected before libvirt is even asked
