"""A server snapshot is a volume snapshot of the root disk that is then uploaded as an image. The compute API used to ask
image-api to import it from a path, which image-api (on another node, and a file-only route) cannot read; the worker that
owns the volume does the export and the upload instead, then drops the snapshot it made for the purpose."""

import pytest

from services.block_store_worker.app import main as w

ARGS = {"snapshot_id": "snap-1", "volume_id": "vol-1", "volume_path": "/dev/vg/vol-abc", "backend": "lvm", "cow_gb": 2,
        "_upload_image": {"name": "web-backup", "project_id": "p7", "visibility": "private", "disk_format": "qcow2"}}


@pytest.fixture
def node(monkeypatch):
    seen = {"calls": [], "post": None, "files": []}
    c = seen["calls"]

    monkeypatch.setattr(w.lvm, "snapshot", lambda lv, snap, gb: c.append(("lvm-snap", lv)) or f"/dev/vg/{snap}")
    monkeypatch.setattr(w.ceph, "snapshot", lambda lv, snap, gb: c.append(("ceph-snap", lv)) or f"rbd:pool/{lv}@{snap}")
    monkeypatch.setattr(w.lvm, "delete", lambda name: c.append(("lvm-del", name)))
    monkeypatch.setattr(w.ceph, "delete_snapshot", lambda path: c.append(("ceph-del", path)))
    monkeypatch.setattr(w, "conductor_update_snapshot", lambda sid, f: c.append(("update", f["status"])))
    monkeypatch.setattr(w, "conductor_delete_snapshot", lambda sid: c.append(("record-deleted", sid)))
    monkeypatch.setattr(w, "conductor_mark_snapshot_error", lambda sid, msg: c.append(("error", msg)))
    monkeypatch.setattr(w, "_image_api_url", lambda args: "http://image.test:8002")
    monkeypatch.setattr(w, "internal_headers", lambda: {"X-Internal-Token": "t"})

    def export(path, backend, dest):
        c.append(("export", path))
        open(dest, "wb").write(b"raw")
        seen["files"].append(dest)

    def convert(cmd, **kw):
        c.append(("convert", cmd[-3:-1] if False else cmd[1]))
        seen["files"].append(cmd[-1])
        open(cmd[-1], "wb").write(b"qcow2")

    monkeypatch.setattr(w, "_export_volume_to_file", export)
    monkeypatch.setattr(w, "run", convert)

    class Resp:
        status_code, text = 201, ""

    def post(url, headers=None, data=None, files=None, timeout=None):
        seen["post"] = {"url": url, "headers": headers, "data": data, "body": files["file"][1].read()}
        c.append(("upload",))
        return seen["resp"] if "resp" in seen else Resp()

    monkeypatch.setattr(w.requests, "post", post)
    return seen


def _names(seen):
    return [x[0] for x in seen["calls"]]


def test_the_snapshot_is_exported_converted_uploaded_and_then_dropped(node):
    w.handle_create_snapshot(dict(ARGS))

    assert _names(node) == ["lvm-snap", "update", "export", "convert", "upload", "lvm-del", "record-deleted"]
    assert node["calls"][2][1].startswith("/dev/vg/snap-"), "the frozen snapshot is exported, not the live volume"


def test_the_image_is_uploaded_for_the_owning_project_as_qcow2(node):
    w.handle_create_snapshot(dict(ARGS))

    post = node["post"]
    assert post["url"] == "http://image.test:8002/images" and post["headers"] == {"X-Internal-Token": "t"}
    assert post["data"] == {"name": "web-backup", "disk_format": "qcow2", "container_format": "bare",
                            "visibility": "private", "project_id": "p7"}
    assert post["body"] == b"qcow2"


def test_a_ceph_snapshot_is_removed_through_rbd(node):
    w.handle_create_snapshot({**ARGS, "backend": "ceph", "volume_path": "rbd:pool/vol-abc"})

    assert _names(node) == ["ceph-snap", "update", "export", "convert", "upload", "ceph-del", "record-deleted"]
    assert node["calls"][2][1].startswith("rbd:pool/vol-abc@")


def test_a_plain_snapshot_is_not_uploaded_and_stays(node):
    args = {k: v for k, v in ARGS.items() if k != "_upload_image"}

    w.handle_create_snapshot(args)

    assert _names(node) == ["lvm-snap", "update"]


def test_a_failed_upload_keeps_the_snapshot_in_error_with_the_reason(node):
    class Conflict:
        status_code, text = 409, "image already exists"

    node["resp"] = Conflict()

    w.handle_create_snapshot(dict(ARGS))

    assert "lvm-del" not in _names(node) and "record-deleted" not in _names(node)
    error = next(x for x in node["calls"] if x[0] == "error")
    assert "409" in error[1] and "image already exists" in error[1]


def test_the_temporary_files_are_gone_after_success_and_after_failure(node, tmp_path):
    import os

    w.handle_create_snapshot(dict(ARGS))
    assert node["files"] and not any(os.path.exists(f) for f in node["files"])

    node["files"].clear()

    class Boom:
        status_code, text = 500, "x"

    node["resp"] = Boom()
    w.handle_create_snapshot(dict(ARGS))
    assert node["files"] and not any(os.path.exists(f) for f in node["files"])


def test_a_ceph_export_goes_to_a_path_that_does_not_exist_yet(monkeypatch, tmp_path):
    # `rbd export` refuses to write over a file ("File exists"), and the callers pass a temporary file they already made
    dest = tmp_path / "out.raw"
    dest.write_bytes(b"")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["existed"] = cmd, dest.exists()

    monkeypatch.setattr(w, "run", fake_run)

    w._export_volume_to_file("rbd:volumes/vol-abc@snap-1", "ceph", str(dest))

    assert seen["cmd"][:2] == ["rbd", "export"] and seen["cmd"][-2:] == ["vol-abc@snap-1", str(dest)]
    assert seen["existed"] is False
