import importlib.util
import random
from pathlib import Path

import pytest
from shared.fs_validate import check_access, check_size
from shared.ganesha_cfg import render

ROOT = Path(__file__).resolve().parents[2]


def load_plan():
    spec = importlib.util.spec_from_file_location("fs_plan", ROOT / "services/file_store_worker/app/plan.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.plan


def test_access_normalizes():
    assert check_access("10.0.0.5", "rw") == ("10.0.0.5/32", "rw")
    assert check_access("10.0.0.7/24", "ro") == ("10.0.0.0/24", "ro")


@pytest.mark.parametrize("to", ["0.0.0.0/0", "10.0.0.0/7", "::/0", "fd00::1", "abc", ""])
def test_access_rejects_wide_or_invalid(to):
    with pytest.raises(ValueError):
        check_access(to, "rw")


def test_access_rejects_bad_level_and_size():
    with pytest.raises(ValueError):
        check_access("10.0.0.5", "admin")
    for gb in (0, 1001, -1):
        with pytest.raises(ValueError):
            check_size(gb)
    assert check_size(5) == 5


def test_render_deny_all_without_rules_and_squash():
    cfg = render("10.0.0.9", [{"id": "a", "path": "/m/a", "rules": []}])
    assert "CLIENT" not in cfg and "Root_Squash" in cfg and "No_Root_Squash" not in cfg
    assert "Bind_addr = 10.0.0.9" in cfg and "Protocols = 4" in cfg


def test_render_levels_and_deterministic():
    shares = [{"id": "b", "path": "/m/b", "rules": [{"to": "10.0.0.5/32", "level": "ro"}]},
              {"id": "a", "path": "/m/a", "rules": [{"to": "10.0.0.0/24", "level": "rw"}]}]
    cfg = render("10.0.0.9", shares)
    assert "Clients = 10.0.0.5/32" in cfg and "Access_Type = RO" in cfg and "Access_Type = RW" in cfg
    random.shuffle(shares)
    assert render("10.0.0.9", shares) == cfg


def test_plan_rules():
    plan = load_plan()
    assert plan({"a": {"status": "creating", "size_gb": 1}}, {}) == [("create", "a")]
    assert plan({"a": {"status": "available", "size_gb": 2}}, {"a": {"size_gb": 1}}) == [("extend", "a", 2)]
    assert plan({"a": {"status": "deleting", "size_gb": 1}}, {"a": {"size_gb": 1}}) == [("delete", "a")]
    assert plan({"a": {"status": "deleting", "size_gb": 1}}, {}) == [("delete", "a")]
    assert plan({"a": {"status": "available", "size_gb": 1}}, {"a": {"size_gb": 1}}) == []
