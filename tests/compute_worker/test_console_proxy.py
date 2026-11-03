"""websockify on a compute node asks this plugin where a console token leads: the loopback VNC port, or nothing."""
import pytest

from shared import console_token
from services.compute_worker.app.console_proxy import ConsoleTokenPlugin


@pytest.fixture
def plugin(monkeypatch):
    monkeypatch.setenv("COMPUTE_HOST", "cloudx-comp-01")
    return ConsoleTokenPlugin(None)


def test_lookup_returns_loopback_target(plugin):
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903)

    assert plugin.lookup(token) == ("127.0.0.1", 5903)


def test_lookup_returns_none_for_invalid(plugin):
    assert plugin.lookup("garbage") is None
    assert plugin.lookup(console_token.issue_token("s1", "cloudx-comp-02", 5903)) is None
    assert plugin.lookup(console_token.issue_token("s1", "cloudx-comp-01", 5903, ttl=-1)) is None


def test_lookup_returns_none_when_the_node_has_no_host_name(monkeypatch):
    monkeypatch.delenv("COMPUTE_HOST", raising=False)
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903)

    assert ConsoleTokenPlugin(None).lookup(token) is None
