"""
Unit tests for replication transfer protocol and LVM replication helpers.
"""

import hmac
import hashlib
import json
import secrets
import socket
import time

import pytest

from services.block_store_worker.app import replication
from services.block_store_worker.app.replication import ReplicationError, serve, send
from services.block_store_worker.app.drivers import lvm


def test_roundtrip_matches_sha256(tmp_path):
    token = "testsecrettoken123"
    vol_id = "vol-test-1"
    size = 1024 * 1024  # 1 MB

    src_file = tmp_path / "source.img"
    data = b"A" * size
    src_file.write_bytes(data)

    target_file = tmp_path / "target.img"
    target_file.write_bytes(b"\x00" * size)

    srv = serve(0, token, lambda ip: ip in ("127.0.0.1", "::1"), open_dev=lambda vid, sz: target_file, lv_size_fn=lambda p: size)
    port = srv.getsockname()[1]

    try:
        local_sha = send(str(src_file), vol_id, size, "127.0.0.1", port, token)
        assert local_sha == replication.hashlib.sha256(data).hexdigest()
        assert target_file.read_bytes() == data
    finally:
        srv.close()


def test_bad_hmac_rejected(tmp_path):
    token = "correcttoken"
    vol_id = "vol-test-2"
    size = 1024 * 1024

    src_file = tmp_path / "source.img"
    src_file.write_bytes(b"B" * size)

    target_file = tmp_path / "target.img"
    target_file.write_bytes(b"\x00" * size)

    called = []
    srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: (called.append(1), target_file)[1], lv_size_fn=lambda p: size)
    port = srv.getsockname()[1]

    try:
        with pytest.raises(ReplicationError) as exc:
            send(str(src_file), vol_id, size, "127.0.0.1", port, "wrongtoken")
        assert "Receiver refused" in str(exc.value) or "transfer failed" in str(exc.value)
        assert target_file.read_bytes() == b"\x00" * size
        assert called == []
    finally:
        srv.close()


def test_digest_mismatch_raises(tmp_path):
    token = "token"
    vol_id = "vol-test-3"
    size = 1024 * 1024

    src_file = tmp_path / "source.img"
    src_file.write_bytes(b"C" * size)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def custom_serve():
        conn, _ = srv.accept()
        with conn:
            fileobj = conn.makefile("rb")
            fileobj.readline(4096)
            remaining = size
            while remaining > 0:
                chunk = fileobj.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
            conn.sendall(b"badfeedbeef0000000000000000000000000000000000000000000000000\n")
        srv.close()

    import threading
    threading.Thread(target=custom_serve, daemon=True).start()

    with pytest.raises(ReplicationError) as exc:
        send(str(src_file), vol_id, size, "127.0.0.1", port, token)
    assert "SHA256 mismatch" in str(exc.value)


def test_peer_not_allowed_rejected(tmp_path):
    token = "token"
    vol_id = "vol-test-4"
    size = 1024

    src_file = tmp_path / "source.img"
    src_file.write_bytes(b"D" * size)

    called = []
    srv = serve(0, token, lambda ip: False, open_dev=lambda vid, sz: (called.append(1), None)[1], lv_size_fn=lambda p: size)
    port = srv.getsockname()[1]

    try:
        with pytest.raises(ReplicationError):
            send(str(src_file), vol_id, size, "127.0.0.1", port, token)
        assert called == []
    finally:
        srv.close()


def test_replayed_nonce_rejected(tmp_path):
    token = "token"
    vol_id = "vol-test-5"
    size = 1024 * 1024

    src_file = tmp_path / "source.img"
    src_file.write_bytes(b"E" * size)
    target_file = tmp_path / "target.img"
    target_file.write_bytes(b"\x00" * size)

    srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: target_file, lv_size_fn=lambda p: size)
    port = srv.getsockname()[1]

    try:
        sha1 = send(str(src_file), vol_id, size, "127.0.0.1", port, token)
        assert sha1

        nonce = secrets.token_hex(16)
        ts = time.time()
        msg_str = f"{vol_id}|{size}|{nonce}|{ts}"
        mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()
        header = json.dumps({"volume_id": vol_id, "size": size, "nonce": nonce, "ts": ts, "mac": mac}) + "\n"

        def manual_send(payload):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            s.sendall(header.encode("utf-8"))
            try:
                s.sendall(payload)
            except (BrokenPipeError, ConnectionResetError):  # the server rejects the replayed nonce and closes before the payload is sent
                pass
            try:
                resp = s.makefile("rb").readline(4096).decode("utf-8")
            except ConnectionResetError:
                resp = ""
            s.close()
            return resp

        resp1 = manual_send(b"\x00" * size)
        assert "ERROR" not in resp1 and resp1.strip()  # a digest

        resp2 = manual_send(b"F" * size)
        # The server answers "ERROR: replayed nonce" and closes while the 1 MiB payload is still unread, which makes the
        # kernel reset the connection; under load the reset can overtake the answer. What matters is that the replayed
        # transfer is refused and writes nothing.
        assert "ERROR" in resp2 or resp2 == ""
        assert target_file.read_bytes() == b"\x00" * size  # what the first use of the nonce wrote; the replay would have written F
    finally:
        srv.close()


def test_server_traversal_volume_id_rejected():
    token = "token"
    vol_id = "../bad"
    size = 1024
    nonce = secrets.token_hex(16)
    ts = time.time()
    msg_str = f"{vol_id}|{size}|{nonce}|{ts}"
    mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()
    header = json.dumps({"volume_id": vol_id, "size": size, "nonce": nonce, "ts": ts, "mac": mac}) + "\n"

    called = []
    srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: (called.append(1), None)[1], lv_size_fn=lambda p: size)
    port = srv.getsockname()[1]

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", port))
        s.sendall(header.encode("utf-8"))
        resp = s.makefile("rb").readline(4096).decode("utf-8")
        s.close()
        assert "ERROR" in resp
        assert called == []
    finally:
        srv.close()


def test_bad_sizes_rejected():
    token = "token"
    vol_id = "vol-good"
    for bad_size in (0, -1, "10", True):
        nonce = secrets.token_hex(16)
        ts = time.time()
        msg_str = f"{vol_id}|{bad_size}|{nonce}|{ts}"
        mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()
        header = json.dumps({"volume_id": vol_id, "size": bad_size, "nonce": nonce, "ts": ts, "mac": mac}) + "\n"

        called = []
        srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: (called.append(1), None)[1], lv_size_fn=lambda p: 1024)
        port = srv.getsockname()[1]

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            s.sendall(header.encode("utf-8"))
            resp = s.makefile("rb").readline(4096).decode("utf-8")
            s.close()
            assert "ERROR" in resp
            assert called == []
        finally:
            srv.close()


def test_size_exceeds_target_rejected():
    token = "token"
    vol_id = "vol-good"
    size = 2048
    nonce = secrets.token_hex(16)
    ts = time.time()
    msg_str = f"{vol_id}|{size}|{nonce}|{ts}"
    mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()
    header = json.dumps({"volume_id": vol_id, "size": size, "nonce": nonce, "ts": ts, "mac": mac}) + "\n"

    called = []
    srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: (called.append(1), None)[1], lv_size_fn=lambda p: 1024)
    port = srv.getsockname()[1]

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", port))
        s.sendall(header.encode("utf-8"))
        resp = s.makefile("rb").readline(4096).decode("utf-8")
        s.close()
        assert "ERROR" in resp
        assert called == []
    finally:
        srv.close()


def test_expired_timestamp_rejected():
    token = "token"
    vol_id = "vol-good"
    size = 1024
    nonce = secrets.token_hex(16)
    ts = time.time() - 120  # expired by 120s
    msg_str = f"{vol_id}|{size}|{nonce}|{ts}"
    mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()
    header = json.dumps({"volume_id": vol_id, "size": size, "nonce": nonce, "ts": ts, "mac": mac}) + "\n"

    called = []
    srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: (called.append(1), None)[1], lv_size_fn=lambda p: 2048)
    port = srv.getsockname()[1]

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", port))
        s.sendall(header.encode("utf-8"))
        resp = s.makefile("rb").readline(4096).decode("utf-8")
        s.close()
        assert "ERROR" in resp
        assert called == []
    finally:
        srv.close()


def test_nan_inf_timestamp_rejected():
    token = "token"
    vol_id = "vol-good"
    size = 1024
    nonce = secrets.token_hex(16)
    for bad_ts in (float('nan'), float('inf'), float('-inf')):
        msg_str = f"{vol_id}|{size}|{nonce}|{bad_ts}"
        mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()
        header = json.dumps({"volume_id": vol_id, "size": size, "nonce": nonce, "ts": bad_ts, "mac": mac}) + "\n"

        called = []
        srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: (called.append(1), None)[1], lv_size_fn=lambda p: 2048)
        port = srv.getsockname()[1]

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            s.sendall(header.encode("utf-8"))
            resp = s.makefile("rb").readline(4096).decode("utf-8")
            s.close()
            assert "ERROR" in resp
            assert called == []
        finally:
            srv.close()


def test_receiver_truncated_stream():
    token = "token"
    vol_id = "vol-trunc"
    size = 4 * 1024 * 1024 + 100
    nonce = secrets.token_hex(16)
    ts = time.time()
    msg_str = f"{vol_id}|{size}|{nonce}|{ts}"
    mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()
    header = json.dumps({"volume_id": vol_id, "size": size, "nonce": nonce, "ts": ts, "mac": mac}) + "\n"

    target_written = []
    class FakeDev:
        def write(self, d):
            target_written.append(d)
        def flush(self):
            pass
        def fileno(self):
            return 1

    srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: FakeDev(), lv_size_fn=lambda p: size)
    port = srv.getsockname()[1]

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", port))
        s.sendall(header.encode("utf-8"))
        s.sendall(b"X" * (4 * 1024 * 1024 + 9))
        s.shutdown(socket.SHUT_WR)
        resp = s.makefile("rb").readline(4096).decode("utf-8")
        s.close()
        assert resp.startswith("ERROR")
    finally:
        srv.close()


def test_stalled_client_timeout():
    token = "token"
    vol_id = "vol-stall"
    size = 1024
    nonce = secrets.token_hex(16)
    ts = time.time()
    msg_str = f"{vol_id}|{size}|{nonce}|{ts}"
    mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()
    header = json.dumps({"volume_id": vol_id, "size": size, "nonce": nonce, "ts": ts, "mac": mac}) + "\n"

    class FakeDev:
        def write(self, d): pass
        def flush(self): pass
        def fileno(self): return 1

    srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: FakeDev(), lv_size_fn=lambda p: 2048, conn_timeout=0.2)
    port = srv.getsockname()[1]

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect(("127.0.0.1", port))
        s.sendall(header.encode("utf-8"))
        
        t0 = time.monotonic()
        data = b""
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pytest.fail("server hung")
        except OSError:
            pass
        elapsed = time.monotonic() - t0
        s.close()

        assert elapsed < 2.0
        assert data == b"" or data.startswith(b"ERROR")
    finally:
        srv.close()


def test_fsync_oserror_propagates(tmp_path):
    token = "token"
    vol_id = "vol-test-fsync"
    size = 1024

    src_file = tmp_path / "source.img"
    src_file.write_bytes(b"X" * size)

    class BadFile:
        def write(self, data):
            pass
        def flush(self):
            pass
        def fileno(self):
            return 9999

    import os
    orig_fsync = os.fsync
    def bad_fsync(fd):
        raise OSError("Disk I/O error")

    os.fsync = bad_fsync
    try:
        srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: BadFile(), lv_size_fn=lambda p: size)
        port = srv.getsockname()[1]

        try:
            with pytest.raises(ReplicationError):
                send(str(src_file), vol_id, size, "127.0.0.1", port, token)
        finally:
            srv.close()
    finally:
        os.fsync = orig_fsync


def test_closed_after_error_reply():
    token = "token"
    vol_id = "../bad"
    size = 1024
    nonce = secrets.token_hex(16)
    ts = time.time()
    msg_str = f"{vol_id}|{size}|{nonce}|{ts}"
    mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()
    header = json.dumps({"volume_id": vol_id, "size": size, "nonce": nonce, "ts": ts, "mac": mac}) + "\n"

    srv = serve(0, token, lambda ip: True, open_dev=lambda vid, sz: None, lv_size_fn=lambda p: 1024)
    port = srv.getsockname()[1]

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(("127.0.0.1", port))
        s.sendall(header.encode("utf-8"))

        # Read response
        fileobj = s.makefile("rb")
        resp = fileobj.readline(4096)
        assert resp.startswith(b"ERROR")

        # After ERROR reply, client's recv/read loop should reach EOF (b"") within 2 seconds
        t0 = time.monotonic()
        eof_reached = False
        while time.monotonic() - t0 < 2.0:
            chunk = fileobj.read(1024)
            if chunk == b"":
                eof_reached = True
                break
        s.close()
        assert eof_reached, "Connection was not closed by server after ERROR reply (no EOF received)"
    finally:
        srv.close()


def test_lvm_replica_helpers(monkeypatch):
    run_calls = []
    monkeypatch.setattr(lvm, "_run", lambda cmd, check=True, timeout=None: run_calls.append(cmd) or "SUCCESS")
    monkeypatch.setattr(lvm, "vg_name", lambda: "vg-test")
    monkeypatch.setattr(lvm, "exists", lambda name: name == "rep-vol-1")
    monkeypatch.setattr(lvm, "delete", lambda name: run_calls.append(["lvremove", name]))
    monkeypatch.setattr(lvm, "succeeds", lambda cmd, **kw: cmd[-1] == "vg-test/rep-vol-1")
    monkeypatch.setattr(lvm, "_cleanup_stale_lv", lambda name, vg: run_calls.append(["_cleanup", name, vg]))

    # replica_create exact argv assert matching lvm.create
    path = lvm.replica_create("vol-1", 10)
    assert path == "/dev/vg-test/rep-vol-1"
    
    expected_lvcreate = [
        "lvcreate", "-y", "-W", "y", "-Z", "y", "-L", "10G", "-n", "rep-vol-1", "vg-test"
    ] + lvm.LVM_CONTAINER_CFG
    assert expected_lvcreate in run_calls

    # replica_delete when exists
    run_calls.clear()
    lvm.replica_delete("vol-1")
    assert ["lvremove", "rep-vol-1"] in run_calls

    # replica_promote when rep exists
    run_calls.clear()
    p = lvm.replica_promote("vol-1", "vol-main")
    assert p == "/dev/vg-test/vol-main"
    assert ["lvrename", "vg-test", "rep-vol-1", "vol-main"] in run_calls

    # replica_promote when rep missing raises RuntimeError
    with pytest.raises(RuntimeError):
        lvm.replica_promote("missing-vol", "vol-main")

    # invalid volume_id validation
    with pytest.raises(ValueError):
        lvm.replica_create("bad/id", 10)
