import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from shared import launcher
from shared.launcher import gunicorn_argv, resolve_workers, run_daemon


def test_workers_precedence():
    assert resolve_workers("image-api", 2, {"workers": "3"}) == 3
    assert resolve_workers("image-api", 2, {}) == 2


@pytest.mark.parametrize("value", ["0", "-1", "abc", ""])
def test_bad_workers_values(value):
    with pytest.raises(ValueError) as exc:
        resolve_workers("image-api", 2, {"workers": value})
    assert str(exc.value) == f"image-api: workers must be a positive integer, got '{value}'"


def _follows(argv, flag, value):
    return argv[argv.index(flag) + 1] == value


def test_nginx_binds_a_unix_socket_and_trusts_no_forwarded_ips():
    argv = gunicorn_argv("/opt/cloudx/services/image", "unix:/run/cloudx/image-api.sock", 2, "nginx")
    assert _follows(argv, "-k", "uvicorn.workers.UvicornWorker")
    assert _follows(argv, "-w", "2")
    assert _follows(argv, "--bind", "unix:/run/cloudx/image-api.sock")
    assert _follows(argv, "--chdir", "/opt/cloudx/services/image")
    assert argv[-1] == "app.main:app"
    assert "--forwarded-allow-ips=" in argv
    assert not any("*" in a for a in argv)


def test_direct_binds_tcp_and_never_passes_a_wildcard_for_forwarded_ips():
    argv = gunicorn_argv("/opt/cloudx/services/object-store", "0.0.0.0:8007", 4, "direct")
    assert _follows(argv, "--bind", "0.0.0.0:8007")
    assert _follows(argv, "-w", "4")
    assert argv[-1] == "app.main:app"
    assert not any("*" in a for a in argv)
    assert not any(a.startswith("--forwarded-allow-ips") for a in argv)


@pytest.mark.parametrize("frontend", ["nginx", "direct"])
def test_control_socket_is_disabled(frontend):
    # gunicorn 26 opens a control socket in $HOME of the service user (/opt/cloudx, root-owned, no-create-home)
    # and logs "Control server error: Permission denied" on every start; nothing here uses it.
    argv = gunicorn_argv("/opt/cloudx/services/image", "0.0.0.0:8002", 1, frontend)
    assert "--no-control-socket" in argv


def test_run_daemon_changes_directory_and_runs_app_main(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "load_service_config", lambda name: calls.append(("config", name)) or {})
    monkeypatch.setattr(launcher.os, "chdir", lambda d: calls.append(("chdir", d)))
    monkeypatch.setattr(launcher.runpy, "run_module", lambda m, run_name=None: calls.append(("run", m, run_name)))
    monkeypatch.setattr(sys, "path", list(sys.path))
    run_daemon("conductor", "/opt/cloudx/services/conductor")
    assert calls == [
        ("config", "conductor"),
        ("chdir", "/opt/cloudx/services/conductor"),
        ("run", "app.main", "__main__"),
    ]
    assert sys.path[0] == "/opt/cloudx/services/conductor"


def test_run_api_picks_bind_and_execs_gunicorn_beside_the_interpreter(monkeypatch):
    execs = []
    monkeypatch.setattr(launcher, "load_service_config", lambda name: {"workers": "3"})
    monkeypatch.setattr(launcher.os, "execv", lambda path, argv: execs.append((path, argv)))
    launcher.run_api("image-api", "/opt/cloudx/services/image", 8002, 2, "nginx")
    launcher.run_api("object-store", "/opt/cloudx/services/object-store", 8007, 2, "direct")
    gunicorn = str(Path(sys.executable).parent / "gunicorn")
    assert execs[0][0] == gunicorn and execs[0][1][0] == gunicorn
    assert _follows(execs[0][1], "--bind", "unix:/run/cloudx/image-api.sock")
    assert _follows(execs[0][1], "-w", "3")
    assert _follows(execs[1][1], "--bind", "0.0.0.0:8007")


def test_run_api_reports_bad_workers_and_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "load_service_config", lambda name: {"workers": "abc"})
    monkeypatch.setattr(launcher.os, "execv", lambda *a: pytest.fail("must not exec"))
    with pytest.raises(SystemExit) as exc:
        launcher.run_api("image-api", "/opt/cloudx/services/image", 8002, 2, "nginx")
    assert exc.value.code == 2
    assert "image-api: workers must be a positive integer, got 'abc'" in capsys.readouterr().err


APP = '''
async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            else:
                await send({"type": "lifespan.shutdown.complete"})
                return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": repr(scope["client"]).encode()})
'''


def _serve_and_get_client(tmp_path, argv):
    """Start gunicorn with argv on a unix socket, send a request with a spoofed X-Forwarded-For, return scope["client"]."""
    gunicorn = Path(sys.executable).parent / "gunicorn"
    sock = tmp_path / "api.sock"
    argv = [a.replace("@SOCK@", str(sock)) for a in argv]
    proc = subprocess.Popen([str(gunicorn), *argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 20
        while not sock.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                proc.terminate()
                pytest.fail(f"gunicorn did not create {sock}:\n{proc.communicate(timeout=10)[0]}")
            time.sleep(0.1)
        out = subprocess.run(
            ["curl", "-sS", "--max-time", "10", "--unix-socket", str(sock), "-H", "X-Forwarded-For: 6.6.6.6",
             "http://localhost/"],
            capture_output=True, text=True, timeout=20,
        )
        assert out.returncode == 0, out.stderr
        return out.stdout
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture
def asgi_dir(tmp_path):
    if not (Path(sys.executable).parent / "gunicorn").exists() or shutil.which("curl") is None:
        pytest.skip("needs gunicorn next to the interpreter and curl")
    pkg = tmp_path / "svc" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text(APP)
    return tmp_path / "svc"


def test_client_supplied_x_forwarded_for_is_ignored_on_a_unix_socket(tmp_path, asgi_dir):
    argv = gunicorn_argv(str(asgi_dir), "unix:@SOCK@", 1, "nginx")
    seen = _serve_and_get_client(tmp_path, argv)
    assert "6.6.6.6" not in seen


def test_the_same_request_is_trusted_when_forwarded_ips_is_widened(tmp_path, asgi_dir):
    """Control for the test above: shows the probe can see a spoofed address at all."""
    argv = [("--forwarded-allow-ips=*" if a == "--forwarded-allow-ips=" else a)
            for a in gunicorn_argv(str(asgi_dir), "unix:@SOCK@", 1, "nginx")]
    seen = _serve_and_get_client(tmp_path, argv)
    assert "6.6.6.6" in seen


def test_nginx_socket_stays_connectable_by_nginx_workers_under_the_unit_umask(asgi_dir):
    """nginx workers are not in the service's group, so the socket must be world-connectable, as gunicorn creates it
    even under UMask=0007. A future --umask 007 would make it 0770 and nginx would answer 502 to every request."""
    run_dir = Path(tempfile.mkdtemp(dir="/tmp"))  # unix socket paths are limited to ~108 bytes
    sock = run_dir / "api.sock"
    gunicorn = Path(sys.executable).parent / "gunicorn"
    argv = [a.replace("@SOCK@", str(sock)) for a in gunicorn_argv(str(asgi_dir), "unix:@SOCK@", 1, "nginx")]
    old = os.umask(0o007)
    try:
        proc = subprocess.Popen([str(gunicorn), *argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    finally:
        os.umask(old)
    try:
        deadline = time.monotonic() + 20
        while not sock.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                proc.terminate()
                pytest.fail(f"gunicorn did not create {sock}:\n{proc.communicate(timeout=10)[0]}")
            time.sleep(0.1)
        mode = stat.S_IMODE(sock.stat().st_mode)
        assert mode & 0o006 == 0o006, (
            f"socket mode is {mode:04o}: nginx workers (not in the service group) could not connect; "
            "do not add --umask to the nginx launcher")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        shutil.rmtree(run_dir, ignore_errors=True)
