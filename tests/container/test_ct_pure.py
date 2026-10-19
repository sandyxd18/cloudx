import importlib.util
from pathlib import Path

import pytest
from shared.ct_validate import check_data_path, check_env, check_image, check_limits

ROOT = Path(__file__).resolve().parents[2]


def load_plan():
    spec = importlib.util.spec_from_file_location("ct_plan", ROOT / "services/container_worker/app/plan.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.plan


@pytest.mark.parametrize("img", ["nginx", "nginx:1.27", "ghcr.io/a/b:tag-1.2", "a/b"])
def test_image_ok(img):
    assert check_image(img) == img


@pytest.mark.parametrize("img", ["nginx; rm", "-v", "Nginx", "a b", "", "a\n", "a:", "$(id)"])
def test_image_rejected(img):
    with pytest.raises(ValueError):
        check_image(img)


def test_env():
    assert check_env({"A_1": "x y"}) == {"A_1": "x y"}
    for bad in ({"1A": "x"}, {"A=B": "x"}, {"A": "x\ny"}, {f"V{i}": "x" for i in range(51)}):
        with pytest.raises(ValueError):
            check_env(bad)


def test_limits():
    assert check_limits(0.5, 128) == (0.5, 128)
    for cpu, mem in ((0.05, 128), (5, 128), (1, 63), (1, 4097)):
        with pytest.raises(ValueError):
            check_limits(cpu, mem)


def test_plan():
    plan = load_plan()
    run = {"a": {"want": "running"}}
    assert plan(run, {}) == [("create", "a")]
    assert plan(run, {"a": "exited"}) == [("start", "a")]
    assert plan(run, {"a": "running"}) == []
    assert plan({"a": {"want": "stopped"}}, {"a": "running"}) == [("stop", "a")]
    assert plan({"a": {"want": "stopped"}}, {"a": "exited"}) == []
    assert plan({"a": {"want": "restart"}}, {"a": "running"}) == [("restart", "a")]
    assert plan({"a": {"want": "restart"}}, {}) == [("create", "a")]
    assert plan({"a": {"want": "deleting"}}, {}) == [("delete", "a")]
    assert plan({"a": {"want": "deleting"}}, {"a": "running"}) == [("delete", "a")]


def test_data_path():
    assert check_data_path("/var/lib/postgresql/data") == "/var/lib/postgresql/data"
    for bad in ("data", "/", "/etc", "/etc/x", "/a/../b", "a b", "/a b", "/proc/1", "/dev/x", ""):
        with pytest.raises(ValueError):
            check_data_path(bad)