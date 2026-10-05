import ast
import logging
import subprocess
from pathlib import Path

import pytest
import requests
from botocore.exceptions import ClientError

from services.block_store_worker.app import reconciler as block_reconciler
from services.image_worker.app import reconciler as image_reconciler
from services.networking.app import reconciler as net_reconciler

ROOT = Path(__file__).resolve().parents[2]

def _is_broad(handler):
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(t, ast.Name) and t.id in ("Exception", "BaseException") for t in types)

def _reports_error(handler):
    names = {handler.name} if handler.name else set()
    for node in (n for stmt in handler.body for n in ast.walk(stmt)):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Name) and node.id in names:
            return True
        if isinstance(node, ast.Call):
            root = node.func
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in ("log", "logger", "logging", "print"):
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("exception", "warning", "error", "echo"):
                return True
    return False

class FakeRPC:
    def __init__(self, volumes=None, snapshots=None):
        self.volumes, self.snapshots, self.casts = volumes or [], snapshots or [], []

    def call(self, target, method, payload, timeout=None):
        return {"volumes": self.volumes} if method == "list_all_volumes" else {"snapshots": self.snapshots}

    def cast(self, target, method, payload):
        self.casts.append((method, payload))

def _volume(status="available", backend="lvm"):
    return {"id": "vol-1", "name": "data", "status": status, "backend": backend}

def _completed(rc, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr=err)

def _network_list(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"networks": [{"id": "n1", "name": "net1", "status": "ACTIVE"}]}

    monkeypatch.setattr(net_reconciler.requests, "get", lambda *a, **k: Resp())
    patched = []
    monkeypatch.setattr(net_reconciler, "_patch_network_status", lambda nid, status: patched.append((nid, status)))
    return patched

def _minio_head_raises(monkeypatch, exc):
    class Client:
        def head_object(self, **kw):
            raise exc

    import boto3

    monkeypatch.setattr(image_reconciler, "IMAGE_STORAGE_BACKEND", "minio")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: Client())

def test_silent_exception_handlers_state_their_reason():
    offenders = []
    for base in ("services", "cli"):
        for path in (ROOT / base).rglob("*.py"):
            if {"node_modules", "__pycache__", "versions"} & set(path.parts):
                continue
            src = path.read_text(encoding="utf-8")
            lines = src.split("\n")
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.ExceptHandler) and _is_broad(node) and not _reports_error(node):
                    if "# best-effort:" not in lines[node.lineno - 1]:
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], "silent `except Exception` without `# best-effort: <reason>`:\n" + "\n".join(offenders)

def test_failed_lvm_check_does_not_mark_volume_error(monkeypatch, caplog):
    def boom(name):
        raise RuntimeError("LVM_VG_NAME is not configured")

    monkeypatch.setattr(block_reconciler.lvm, "exists", boom)
    rpc = FakeRPC(volumes=[_volume()])
    with caplog.at_level(logging.WARNING):
        block_reconciler.reconcile_volumes(rpc)
    assert rpc.casts == []
    assert "cannot check lvm object" in caplog.text

def test_failed_check_does_not_delete_record_of_deleting_volume(monkeypatch):
    monkeypatch.setattr(block_reconciler.lvm, "exists", lambda name: (_ for _ in ()).throw(OSError("lvdisplay missing")))
    rpc = FakeRPC(volumes=[_volume(status="deleting")])
    block_reconciler.reconcile_volumes(rpc)
    assert rpc.casts == []

def test_really_missing_backend_object_still_marks_volume_error(monkeypatch):
    monkeypatch.setattr(block_reconciler.lvm, "exists", lambda name: False)
    rpc = FakeRPC(volumes=[_volume()])
    block_reconciler.reconcile_volumes(rpc)
    assert [m for m, _ in rpc.casts] == ["mark_volume_error"]

def test_failed_snapshot_check_does_not_mark_snapshot_error(monkeypatch):
    monkeypatch.setattr(block_reconciler.ceph, "exists", lambda name: (_ for _ in ()).throw(RuntimeError("rbd down")))
    rpc = FakeRPC(snapshots=[{"id": "snap-1", "status": "available", "backend": "ceph"}])
    block_reconciler.reconcile_snapshots(rpc)
    assert rpc.casts == []

def test_ovn_exists_distinguishes_missing_from_unreachable(monkeypatch):
    monkeypatch.setattr(net_reconciler, "run", lambda *a, **k: _completed(0, "name : net1\n"))
    assert net_reconciler._ovn_exists("Logical_Switch", "net1") is True
    monkeypatch.setattr(net_reconciler, "run", lambda *a, **k: _completed(0, ""))
    assert net_reconciler._ovn_exists("Logical_Switch", "net1") is False
    monkeypatch.setattr(net_reconciler, "run", lambda *a, **k: _completed(1, "", "connection refused"))
    assert net_reconciler._ovn_exists("Logical_Switch", "net1") is None
    # ovn-nbctl missing (127) or hanging until the helper's timeout (124) is "unknown", not "missing"
    for rc in (127, 124):
        monkeypatch.setattr(net_reconciler, "run", lambda *a, rc=rc, **k: _completed(rc, "", "boom"))
        assert net_reconciler._ovn_exists("Logical_Switch", "net1") is None

def test_unreachable_ovn_does_not_mark_active_network_error(monkeypatch):
    patched = _network_list(monkeypatch)
    monkeypatch.setattr(net_reconciler, "_ovn_exists", lambda table, name: None)
    net_reconciler.reconcile_networks()
    assert patched == []

def test_missing_ovn_switch_marks_active_network_error(monkeypatch):
    patched = _network_list(monkeypatch)
    monkeypatch.setattr(net_reconciler, "_ovn_exists", lambda table, name: False)
    net_reconciler.reconcile_networks()
    assert patched == [("n1", "error")]

def test_minio_404_means_image_is_gone(monkeypatch):
    _minio_head_raises(monkeypatch, ClientError({"Error": {"Code": "404"}}, "HeadObject"))
    assert image_reconciler._image_exists("cirros.qcow2") is False

def test_minio_outage_is_unknown_not_missing(monkeypatch):
    _minio_head_raises(monkeypatch, requests.ConnectionError("minio down"))
    assert image_reconciler._image_exists("cirros.qcow2") is None

def test_access_key_exchange_failure_is_logged(monkeypatch, caplog):
    from fastapi import Request
    from shared import auth_middleware

    def down(*a, **k):
        raise requests.ConnectionError("auth-api down")

    monkeypatch.setattr(auth_middleware.requests, "post", down)
    request = Request({"type": "http", "headers": [(b"x-access-key-id", b"k"), (b"x-secret-access-key", b"s")]})
    with caplog.at_level(logging.WARNING):
        assert auth_middleware._exchange_access_key(request) is None
    assert "access-key exchange" in caplog.text

@pytest.mark.parametrize("stored", ["not-a-bcrypt-hash", "", None])
def test_malformed_password_hash_is_a_failed_login_not_a_crash(stored):
    from services.auth.app.tokens import _verify_password

    assert _verify_password("secret", stored) is False
