"""
Replication transfer protocol and LVM helper functions for CloudX block-store.
"""

from __future__ import annotations

import io
import math
import hmac
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from collections import deque
from typing import Callable, Any

from shared.proc import run
from .drivers import lvm

log = logging.getLogger(__name__)

class ReplicationError(Exception):
    """Raised when replication transfer fails."""
    pass


# ── TCP Replication Server & Sender ──────────────────────────────────────────

_nonce_cache: deque[str] = deque(maxlen=256)
_nonce_lock = threading.Lock()


def _lv_size(dev_path: str) -> int:
    """Get device size in bytes via blockdev."""
    res = run(["blockdev", "--getsize64", dev_path], check=True)
    return int(res.stdout.strip())


def serve(
    port: int,
    token: str,
    allowed_peer: Callable[[str], bool],
    open_dev: Callable[[str, int], Any] | None = None,
    lv_size_fn: Callable[[str], int] | None = None,
    conn_timeout: float = 30.0,
) -> socket.socket:
    """Start a daemon TCP server accepting replication streams."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(5)
    log.info("Replication server listening on port %d", port)

    def _server_loop():
        while True:
            try:
                if srv.fileno() == -1:
                    break
                srv.settimeout(1.0)
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except (OSError, socket.error):
                    if srv.fileno() == -1:
                        break
                    continue
                peer_ip = addr[0]
                if not allowed_peer(peer_ip):
                    log.warning("Replication connection from disallowed peer %s", peer_ip)
                    conn.close()
                    continue
                threading.Thread(
                    target=_handle_peer,
                    args=(conn, peer_ip, token, open_dev, lv_size_fn, conn_timeout),
                    daemon=True,
                ).start()
            except Exception:  # best-effort: server loop recovery on accept error
                time.sleep(0.1)

    t = threading.Thread(target=_server_loop, daemon=True)
    t.start()
    return srv


def _write_stream(fileobj: Any, out_file: Any, size: int) -> str:
    """Shared write-loop helper: read exactly size bytes in 4 MiB chunks, hash, flush, fsync."""
    hasher = hashlib.sha256()
    chunk_size = 4 * 1024 * 1024  # 4 MiB
    remaining = size
    while remaining > 0:
        to_read = min(remaining, chunk_size)
        chunk = fileobj.read(to_read)
        if not chunk:
            raise ReplicationError("Truncated stream: received fewer bytes than expected size")
        out_file.write(chunk)
        hasher.update(chunk)
        remaining -= len(chunk)
    if hasattr(out_file, "flush"):
        out_file.flush()
    if hasattr(out_file, "fileno"):
        try:
            os.fsync(out_file.fileno())
        except (AttributeError, io.UnsupportedOperation):
            pass
    return hasher.hexdigest()


def _handle_peer(
    conn: socket.socket,
    peer_ip: str,
    token: str,
    open_dev: Callable | None,
    lv_size_fn: Callable | None,
    conn_timeout: float,
) -> None:
    conn.settimeout(conn_timeout)
    with conn, conn.makefile("rb") as fileobj:
        try:
            line = fileobj.readline(4096)
            if not line:
                return
            header = json.loads(line.decode("utf-8"))
            volume_id = header.get("volume_id")
            size = header.get("size")
            nonce = header.get("nonce")
            ts = header.get("ts")
            received_mac = header.get("mac")

            # Validate volume_id
            if not isinstance(volume_id, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", volume_id):
                conn.sendall(b"ERROR: invalid volume_id\n")
                return

            # Validate size (reject bool, negative, non-int)
            if type(size) is not int or size <= 0:
                conn.sendall(b"ERROR: invalid size\n")
                return

            # Validate ts (reject bool, non-float/int, NaN/inf)
            if type(ts) not in (int, float) or not math.isfinite(ts) or abs(time.time() - ts) > 60:
                conn.sendall(b"ERROR: expired or invalid timestamp\n")
                return

            # Validate nonce and mac types
            if not isinstance(nonce, str) or not isinstance(received_mac, str):
                conn.sendall(b"ERROR: invalid header fields\n")
                return

            # Verify HMAC
            msg_str = f"{volume_id}|{size}|{nonce}|{ts}"
            expected_mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(received_mac, expected_mac):
                log.warning("Replication HMAC mismatch from %s", peer_ip)
                conn.sendall(b"ERROR: invalid mac\n")
                return

            # Replay protection
            with _nonce_lock:
                if nonce in _nonce_cache:
                    log.warning("Replication replayed nonce from %s", peer_ip)
                    conn.sendall(b"ERROR: replayed nonce\n")
                    return
                _nonce_cache.append(nonce)

            vg = lvm.vg_name() if lvm.LVM_VG_NAME else "vg-cloudx"
            dev_path = f"/dev/{vg}/rep-{volume_id}"

            # Determine target device size without opening device for writing yet
            get_size = lv_size_fn or _lv_size
            target_size = get_size(dev_path)

            if size > target_size:
                conn.sendall(b"ERROR: size exceeds target device capacity\n")
                return

            # Call open_dev AT MOST ONCE
            if open_dev is not None:
                f = open_dev(volume_id, size)
                if hasattr(f, "write"):
                    computed_sha = _write_stream(fileobj, f, size)
                else:
                    with open(f, "wb") as out:
                        computed_sha = _write_stream(fileobj, out, size)
            else:
                with open(dev_path, "wb") as out:
                    computed_sha = _write_stream(fileobj, out, size)

            conn.sendall((computed_sha + "\n").encode("utf-8"))
        except Exception as exc:
            log.error("Error handling replication peer %s: %s", peer_ip, exc, exc_info=True)
            try:
                conn.sendall(b"ERROR: transfer failed\n")
            except Exception:  # best-effort: ignore socket close errors during error reply
                pass


def send(dev_path: str, volume_id: str, size: int, host: str, port: int, token: str) -> str:
    """Stream volume data to receiver over TCP with HMAC auth; return local sha256."""
    if not isinstance(volume_id, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", volume_id):
        raise ValueError(f"Invalid volume_id: {volume_id}")
    if type(size) is not int or size <= 0:
        raise ValueError(f"Invalid size: {size}")

    nonce = secrets.token_hex(16)
    ts = time.time()
    msg_str = f"{volume_id}|{size}|{nonce}|{ts}"
    mac = hmac.new(token.encode("utf-8"), msg_str.encode("utf-8"), hashlib.sha256).hexdigest()

    header = json.dumps({
        "volume_id": volume_id,
        "size": size,
        "nonce": nonce,
        "ts": ts,
        "mac": mac,
    }) + "\n"

    hasher = hashlib.sha256()
    chunk_size = 4 * 1024 * 1024  # 4 MiB

    try:
        with socket.create_connection((host, port), timeout=60.0) as s:
            s.sendall(header.encode("utf-8"))

            with open(dev_path, "rb") as f:
                remaining = size
                while remaining > 0:
                    to_read = min(remaining, chunk_size)
                    chunk = f.read(to_read)
                    if not chunk:
                        raise ReplicationError("Source file read fewer bytes than expected size")
                    s.sendall(chunk)
                    hasher.update(chunk)
                    remaining -= len(chunk)

            fileobj = s.makefile("rb")
            resp_line = fileobj.readline(4096).decode("utf-8").strip()

        if resp_line.startswith("ERROR"):
            raise ReplicationError(f"Receiver refused replication: {resp_line}")

        local_sha = hasher.hexdigest()
        if resp_line != local_sha:
            raise ReplicationError(f"SHA256 mismatch: local {local_sha} != remote {resp_line}")

        return local_sha
    except ReplicationError:
        raise
    except Exception as exc:
        raise ReplicationError(f"Replication transfer failed to {host}:{port}: {exc}") from exc

# ponytail: full copy every sync; incremental via thin_delta bila perlu
# Note: stream is unencrypted over TCP; HMAC authenticates only.
