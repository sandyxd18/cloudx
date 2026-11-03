"""get_console_info tells compute-api which node the VNC port is on (`host`, the name the token is bound to) and which
address browsers use to reach that node's websockify (`proxy_host`)."""
import importlib

import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import main  # noqa: E402


@pytest.fixture(autouse=True)
def vnc(monkeypatch):
    monkeypatch.setattr(main.libvirt_driver, "vnc_info", lambda name: {"status": "available", "listen": "127.0.0.1", "port": 5901})


def test_console_info_names_the_node_and_its_proxy_address(monkeypatch):
    monkeypatch.setattr(main, "COMPUTE_HOST", "node-1")
    monkeypatch.setattr(main, "CONSOLE_PROXY_HOST", "203.0.113.5")

    info = main.handle_get_console_info({"libvirt_domain_name": "vm1"})

    assert info["port"] == 5901
    assert (info["host"], info["proxy_host"]) == ("node-1", "203.0.113.5")


@pytest.fixture
def reloaded_config(monkeypatch):
    """The worker config module re-read under the environment the test sets, and again under the real one afterwards."""
    from services.compute_worker.app import config

    yield lambda: importlib.reload(config)
    monkeypatch.undo()
    importlib.reload(config)


def test_the_proxy_address_defaults_to_the_node_name(monkeypatch, reloaded_config):
    monkeypatch.setenv("COMPUTE_HOST", "node-2")
    monkeypatch.delenv("CONSOLE_PROXY_HOST", raising=False)

    assert reloaded_config().CONSOLE_PROXY_HOST == "node-2"


def test_the_proxy_address_can_be_set(monkeypatch, reloaded_config):
    monkeypatch.setenv("COMPUTE_HOST", "node-2")
    monkeypatch.setenv("CONSOLE_PROXY_HOST", "10.1.2.3")

    assert reloaded_config().CONSOLE_PROXY_HOST == "10.1.2.3"
