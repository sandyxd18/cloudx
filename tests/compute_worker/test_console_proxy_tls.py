"""The console proxy serves https when `console_proxy_cert` and `console_proxy_key` are both set in compute-worker.conf, and
http otherwise. The worker reports the scheme with the proxy address, so the link compute-api hands out matches what the
proxy serves. One of the two settings alone, or a file that cannot be read, stops the proxy with a message instead of
serving http under an https link."""
import pytest

pytest.importorskip("fastapi")

from services.compute_worker.app import console_proxy, main  # noqa: E402


@pytest.fixture
def pair(tmp_path):
    cert, key = tmp_path / "node.crt", tmp_path / "node.key"
    cert.write_text("cert")
    key.write_text("key")
    return str(cert), str(key)


@pytest.fixture(autouse=True)
def no_tls_env(monkeypatch):
    monkeypatch.delenv("CONSOLE_PROXY_CERT", raising=False)
    monkeypatch.delenv("CONSOLE_PROXY_KEY", raising=False)
    monkeypatch.setattr(console_proxy, "load_service_config", lambda name: {})


# ── the rule ──────────────────────────────────────────────────────────────────

def test_both_settings_give_the_files(monkeypatch, pair):
    monkeypatch.setenv("CONSOLE_PROXY_CERT", pair[0])
    monkeypatch.setenv("CONSOLE_PROXY_KEY", pair[1])
    assert console_proxy.tls_files() == pair


@pytest.mark.parametrize("cert, key", [(None, None), ("", ""), (" ", None)])
def test_no_settings_mean_plain_http(monkeypatch, cert, key):
    for name, value in (("CONSOLE_PROXY_CERT", cert), ("CONSOLE_PROXY_KEY", key)):
        if value is not None:
            monkeypatch.setenv(name, value)
    assert console_proxy.tls_files() is None


@pytest.mark.parametrize("only, missing", [("CONSOLE_PROXY_CERT", "console_proxy_key"), ("CONSOLE_PROXY_KEY", "console_proxy_cert")])
def test_one_setting_alone_is_an_error_naming_the_other(monkeypatch, only, missing):
    monkeypatch.setenv(only, "/etc/cloudx/x")
    with pytest.raises(ValueError, match=missing):
        console_proxy.tls_files()


# ── the websockify command ────────────────────────────────────────────────────

def test_without_tls_the_command_is_the_one_the_unit_used_to_run():
    assert console_proxy.proxy_args(None) == ["--web", "/usr/share/novnc", "--token-plugin", "console_proxy.ConsoleTokenPlugin", "6080"]


def test_with_tls_websockify_gets_the_files_and_refuses_plain_connections(pair):
    args = console_proxy.proxy_args(pair)
    assert args[args.index("--cert") + 1] == pair[0] and args[args.index("--key") + 1] == pair[1]
    assert "--ssl-only" in args, "a plain http connection must not be served: the token is in the URL"
    assert args[-1] == "6080"


# ── the launcher ──────────────────────────────────────────────────────────────

@pytest.fixture
def launched(monkeypatch):
    seen = []
    monkeypatch.setattr(console_proxy, "_run", seen.append)
    return seen


def test_the_launcher_starts_websockify_with_tls_when_configured(monkeypatch, launched, pair):
    monkeypatch.setenv("CONSOLE_PROXY_CERT", pair[0])
    monkeypatch.setenv("CONSOLE_PROXY_KEY", pair[1])
    console_proxy.main()
    assert launched == [console_proxy.proxy_args(pair)]


def test_the_launcher_starts_plain_http_without_settings(launched):
    console_proxy.main()
    assert launched == [console_proxy.proxy_args(None)]


def test_a_file_that_cannot_be_read_stops_the_proxy(monkeypatch, launched, pair, tmp_path):
    monkeypatch.setenv("CONSOLE_PROXY_CERT", pair[0])
    monkeypatch.setenv("CONSOLE_PROXY_KEY", str(tmp_path / "gone.key"))
    with pytest.raises(SystemExit) as stop:
        console_proxy.main()
    assert "gone.key" in str(stop.value) and launched == []


def test_one_setting_alone_stops_the_proxy(monkeypatch, launched):
    monkeypatch.setenv("CONSOLE_PROXY_CERT", "/etc/cloudx/x.crt")
    with pytest.raises(SystemExit) as stop:
        console_proxy.main()
    assert "console_proxy_key" in str(stop.value) and launched == []


# ── what the worker reports ───────────────────────────────────────────────────

@pytest.fixture
def vnc(monkeypatch):
    monkeypatch.setattr(main.libvirt_driver, "vnc_info", lambda name: {"status": "available", "listen": "127.0.0.1", "port": 5901})


def test_the_worker_reports_https_when_the_proxy_has_a_certificate(monkeypatch, vnc, pair):
    monkeypatch.setenv("CONSOLE_PROXY_CERT", pair[0])
    monkeypatch.setenv("CONSOLE_PROXY_KEY", pair[1])
    assert main.handle_get_console_info({"libvirt_domain_name": "vm1"})["proxy_scheme"] == "https"


def test_the_worker_reports_http_without_a_certificate(vnc):
    assert main.handle_get_console_info({"libvirt_domain_name": "vm1"})["proxy_scheme"] == "http"


def test_the_worker_reports_http_when_the_settings_are_incomplete(monkeypatch, vnc):
    monkeypatch.setenv("CONSOLE_PROXY_CERT", "/etc/cloudx/x.crt")
    assert main.handle_get_console_info({"libvirt_domain_name": "vm1"})["proxy_scheme"] == "http"


# ── the unit ──────────────────────────────────────────────────────────────────

def test_the_unit_starts_the_launcher_with_the_modules_on_the_path():
    from pathlib import Path

    unit = (Path(__file__).resolve().parents[2] / "etc/systemd/cloudx-novnc-proxy.service").read_text().splitlines()

    assert "ExecStart=/opt/cloudx/venv/bin/python -m console_proxy" in unit
    path = next(line for line in unit if line.startswith("Environment=PYTHONPATH="))
    assert "/opt/cloudx/services/compute_worker/app" in path and "/opt/cloudx/services" in path.split(":")
