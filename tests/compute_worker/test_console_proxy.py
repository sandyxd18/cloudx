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


def test_lookup_uses_the_same_node_name_as_the_worker(monkeypatch):
    """The token's host is the worker's COMPUTE_HOST: COMPUTE_HOST, else NODE_NAME, else cloudx-compute-01 (config.py)."""
    monkeypatch.delenv("COMPUTE_HOST", raising=False)
    monkeypatch.delenv("NODE_NAME", raising=False)
    default_token = console_token.issue_token("s1", "cloudx-compute-01", 5903)
    node_token = console_token.issue_token("s1", "edge-7", 5903)

    assert ConsoleTokenPlugin(None).lookup(default_token) == ("127.0.0.1", 5903)
    monkeypatch.setenv("NODE_NAME", "edge-7")
    assert ConsoleTokenPlugin(None).lookup(node_token) == ("127.0.0.1", 5903)
    assert ConsoleTokenPlugin(None).lookup(default_token) is None
    monkeypatch.setenv("COMPUTE_HOST", "cloudx-comp-01")
    assert ConsoleTokenPlugin(None).lookup(node_token) is None


def test_lookup_returns_none_when_the_node_name_is_empty(monkeypatch):
    monkeypatch.setenv("COMPUTE_HOST", "")
    token = console_token.issue_token("s1", "", 5903)

    assert ConsoleTokenPlugin(None).lookup(token) is None


@pytest.mark.parametrize("secret", [None, ""])
def test_lookup_without_a_secret_returns_none(monkeypatch, secret):
    token = console_token.issue_token("s1", "cloudx-comp-01", 5903)
    monkeypatch.setenv("COMPUTE_HOST", "cloudx-comp-01")
    if secret is None:
        monkeypatch.delenv("AUTH_SECRET_KEY")
    else:
        monkeypatch.setenv("AUTH_SECRET_KEY", secret)

    assert ConsoleTokenPlugin(None).lookup(token) is None
