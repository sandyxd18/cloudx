import ast
import dataclasses
from pathlib import Path

import pytest

from cloudx_deb.manifest import load_manifest
from cloudx_deb.render import render_api_unit, render_daemon_unit, render_nginx, render_wrapper

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "etc" / "systemd"
GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture(scope="module")
def by_name():
    return {u.name: u for u in load_manifest()}


@pytest.fixture
def image(by_name):
    return dataclasses.replace(by_name["image-api"], frontend="nginx")


@pytest.fixture
def auth(by_name):
    return dataclasses.replace(by_name["auth-api"], frontend="nginx")


@pytest.fixture
def object_store(by_name):
    return dataclasses.replace(by_name["object-store-api"], frontend="direct")


def golden(name):
    return (GOLDEN / name).read_text()


def lines(text):
    return text.splitlines()


def _call(text, func):
    """Keyword arguments of the single call to `func` in the wrapper source, as literals."""
    calls = [n for n in ast.walk(ast.parse(text))
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == func]
    assert len(calls) == 1
    assert calls[0].args == []
    return {k.arg: ast.literal_eval(k.value) for k in calls[0].keywords}


def test_api_unit_goldens(image, auth, object_store):
    assert render_api_unit(image) == golden("cloudx-image-api.service")
    assert render_api_unit(auth) == golden("cloudx-auth-api.service")
    assert render_api_unit(object_store) == golden("cloudx-object-store-api.service")


def test_api_unit_nginx_fields(auth):
    text = render_api_unit(auth)
    for line in ("Type=simple", "User=cloudx", "Group=cloudx", "UMask=0007",
                 "WorkingDirectory=/opt/cloudx/services/auth",
                 "ExecStart=/usr/bin/cloudx-auth-api", "Restart=always", "RestartSec=5s",
                 "After=network.target postgresql.service", "Wants=postgresql.service",
                 "WantedBy=multi-user.target"):
        assert line in lines(text), line
    assert "gunicorn" not in text


def test_api_unit_keeps_root_user_with_cloudx_group(by_name):
    text = render_api_unit(dataclasses.replace(by_name["block-store-api"], frontend="nginx"))
    assert "User=root" in lines(text)
    assert "Group=cloudx" in lines(text)
    assert "ExecStart=/usr/bin/cloudx-block-store-api" in lines(text)


def test_api_unit_is_uniform_for_direct_frontend(object_store):
    nginx = dataclasses.replace(object_store, frontend="nginx")
    assert render_api_unit(object_store) == render_api_unit(nginx)
    assert "UMask=0007" in lines(render_api_unit(object_store))


def test_api_unit_rejects_other_kinds(by_name):
    with pytest.raises(ValueError):
        render_api_unit(by_name["compute-worker"])


def test_daemon_unit_golden_and_only_execstart_changes(by_name):
    u = by_name["compute-worker"]
    source = (SYSTEMD / u.unit_file).read_text()
    out = render_daemon_unit(u, source)
    assert out == golden("cloudx-compute-worker.service")
    old, new = lines(source), lines(out)
    assert len(old) == len(new)
    changed = [(a, b) for a, b in zip(old, new) if a != b]
    assert changed == [("ExecStart=/opt/cloudx/venv/bin/python -m app.main",
                        "ExecStart=/usr/bin/cloudx-compute-worker")]
    assert out.endswith("\n") == source.endswith("\n")


def test_daemon_unit_replaces_every_daemon_source(by_name):
    for u in (u for u in by_name.values() if u.kind == "daemon"):
        source = (SYSTEMD / u.unit_file).read_text()
        out = render_daemon_unit(u, source)
        assert [ln for ln in lines(out) if ln.startswith("ExecStart=")] == [
            f"ExecStart=/usr/bin/cloudx-{u.name}"], u.name


def test_daemon_unit_needs_a_daemon_and_one_execstart(by_name):
    with pytest.raises(ValueError):
        render_daemon_unit(by_name["compute-worker"], "[Service]\nType=simple\n")
    with pytest.raises(ValueError):
        render_daemon_unit(by_name["image-api"], "[Service]\nExecStart=/x\n")


def test_nginx_goldens(image, auth):
    assert render_nginx(image) == golden("cloudx-image-api.conf")
    assert render_nginx(auth) == golden("cloudx-auth-api.conf")


def test_nginx_snippet_contents(image):
    text = render_nginx(image)
    assert "upstream cloudx_image_api {" in text
    assert "server unix:/run/cloudx/image-api.sock;" in text
    assert "listen 8002;" in text
    assert "location / {" in text
    assert "proxy_pass http://cloudx_image_api;" in text
    assert "proxy_http_version 1.1;" in text
    assert "proxy_set_header Host $host;" in text
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in text


def test_nginx_never_appends_to_forwarded_for(image, auth):
    for u in (image, auth):
        assert "$proxy_add_x_forwarded_for" not in render_nginx(u)


def test_only_streaming_units_lift_body_limits(image, auth):
    streaming = render_nginx(image)
    for line in ("client_max_body_size 0;", "proxy_request_buffering off;",
                 "proxy_read_timeout 3600;"):
        assert line in streaming
        assert line not in render_nginx(auth)


def test_nginx_upstream_names_are_valid_identifiers(by_name):
    for u in (u for u in by_name.values() if u.kind == "api"):
        text = render_nginx(dataclasses.replace(u, frontend="nginx"))
        name = text.split("upstream ", 1)[1].split(" {", 1)[0]
        assert name.isidentifier(), u.name
        assert f"proxy_pass http://{name};" in text


def test_nginx_rejects_direct_unit(object_store, by_name):
    with pytest.raises(ValueError):
        render_nginx(object_store)
    with pytest.raises(ValueError):
        render_nginx(by_name["compute-worker"])


def test_api_wrapper(image):
    text = render_wrapper(image)
    assert text.startswith("#!/opt/cloudx/venv/bin/python\n")
    compile(text, "cloudx-image-api", "exec")
    assert _call(text, "run_api") == {
        "name": "image-api", "app_dir": "/opt/cloudx/services/image", "port": 8002,
        "workers": image.workers, "frontend": "nginx",
    }
    assert "from shared.launcher import run_api" in text


def test_api_wrapper_direct_frontend(object_store):
    assert _call(render_wrapper(object_store), "run_api") == {
        "name": "object-store-api", "app_dir": "/opt/cloudx/services/object_store", "port": 8007,
        "workers": 2, "frontend": "direct",
    }


def test_daemon_wrapper(by_name):
    text = render_wrapper(by_name["compute-worker"])
    assert text.startswith("#!/opt/cloudx/venv/bin/python\n")
    compile(text, "cloudx-compute-worker", "exec")
    assert _call(text, "run_daemon") == {
        "name": "compute-worker", "app_dir": "/opt/cloudx/services/compute_worker",
    }
    assert "run_api" not in text


def test_wrapper_rejects_plain_units(by_name):
    with pytest.raises(ValueError):
        render_wrapper(by_name["named-auth"])


def test_every_wrapper_compiles(by_name):
    for u in (u for u in by_name.values() if u.kind != "unit"):
        compile(render_wrapper(u), u.name, "exec")
