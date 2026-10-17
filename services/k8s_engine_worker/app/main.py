"""k8s-engine-worker: manages lifecycle and VM provisioning for managed Kubernetes clusters."""
from __future__ import annotations

from shared.config import load_service_config

load_service_config("k8s-engine-worker")

import datetime
import logging
import os
import time

import requests
from shared.internal import internal_headers
from shared.k8s_cfg import render_master, render_worker

log = logging.getLogger("k8s-engine-worker")
API = os.getenv("K8S_ENGINE_API_URL", "http://127.0.0.1:8015").rstrip("/")
COMPUTE_API = os.getenv("COMPUTE_API_URL", "http://127.0.0.1:8003").rstrip("/")
CALLBACK_URL = os.getenv("CALLBACK_URL", "http://127.0.0.1:8015")
VM_IMAGE = os.getenv("VM_IMAGE", "img-k3s-default")
MASTER_FLAVOR = os.getenv("MASTER_FLAVOR", "m1.medium")
WORKER_FLAVOR = os.getenv("WORKER_FLAVOR", "m1.small")
INTERVAL = int(os.getenv("K8S_ENGINE_WORKER_INTERVAL", "5"))
READY_TIMEOUT = int(os.getenv("READY_TIMEOUT", "900"))


def _create_vm(name: str, flavor: str, network: str, user_data: str) -> str:
    payload = {"name": name, "image": VM_IMAGE, "flavor": flavor, "network": network, "user_data": user_data}
    r = requests.post(f"{COMPUTE_API}/servers", json=payload, headers=internal_headers(), timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def _delete_vm(server_id: str) -> None:
    r = requests.delete(f"{COMPUTE_API}/servers/{server_id}", headers=internal_headers(), timeout=30)
    if r.status_code != 404:  # already gone is fine
        r.raise_for_status()


def _record_node(cluster_id: str, server_id: str, role: str) -> None:
    r = requests.post(f"{API}/internal/k8s-engine/clusters/{cluster_id}/nodes",
                      json={"server_id": server_id, "role": role}, headers=internal_headers(), timeout=10)
    r.raise_for_status()


def _delete_node_record(cluster_id: str, node_id: str) -> None:
    r = requests.delete(f"{API}/internal/k8s-engine/clusters/{cluster_id}/nodes/{node_id}", headers=internal_headers(), timeout=10)
    if r.status_code != 404:
        r.raise_for_status()


def _set_status(cluster_id: str, status: str, reason: str | None = None) -> None:
    payload = {"status": status}
    if reason is not None:
        payload["status_reason"] = reason
    r = requests.post(f"{API}/internal/k8s-engine/clusters/{cluster_id}/status", json=payload, headers=internal_headers(), timeout=10)
    r.raise_for_status()


def _spawn(cluster: dict, role: str, name: str, flavor: str, user_data: str) -> None:
    """Create the VM, then record it; a VM that cannot be recorded would be invisible to cleanup, so drop it."""
    server_id = _create_vm(name, flavor, cluster["network_id"], user_data)
    try:
        _record_node(cluster["id"], server_id, role)
    except Exception:  # best-effort: the original error is re-raised after the orphan VM is removed
        _delete_vm(server_id)
        raise


def _spawn_workers(cluster: dict, master_ip: str, have: int, want: int) -> None:
    token = os.getenv("INTERNAL_TOKEN", "")
    for n in range(have + 1, want + 1):
        ud = render_worker(cluster["id"], master_ip, cluster["join_token"], CALLBACK_URL, token)
        _spawn(cluster, "worker", f"{cluster['name']}-{cluster['id'][-6:]}-worker-{n}", WORKER_FLAVOR, ud)


def _expired(cluster: dict) -> bool:
    created = datetime.datetime.fromisoformat(cluster["created_at"].replace("Z", "+00:00"))
    if created.tzinfo is None:  # the API stores naive UTC
        created = created.replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds() > READY_TIMEOUT


def reconcile_cluster(cluster: dict) -> None:
    cid, status, nodes = cluster["id"], cluster["status"], cluster.get("nodes", [])
    masters = [n for n in nodes if n["role"] == "master"]
    workers = [n for n in nodes if n["role"] == "worker"]
    want = cluster.get("worker_count", 0)
    master_ip = cluster.get("master_ip") or next((n["ip"] for n in masters if n["ip"]), None)

    if status == "DELETING":
        for n in nodes:
            _delete_vm(n["server_id"])  # a failure keeps the row so the next tick retries
        r = requests.delete(f"{API}/internal/k8s-engine/clusters/{cid}", headers=internal_headers(), timeout=10)
        if r.status_code != 404:
            r.raise_for_status()
        return

    if status == "CREATING":
        # ponytail: no timeout for SCALING (no scale start time stored); add a column when stuck scales matter
        if _expired(cluster):
            _set_status(cid, "ERROR", "timeout waiting for nodes")
            return
        if not masters:
            ud = render_master(cid, cluster["name"], cluster["join_token"], cluster["admin_token"], CALLBACK_URL, os.getenv("INTERNAL_TOKEN", ""))
            _spawn(cluster, "master", f"{cluster['name']}-{cid[-6:]}-master", MASTER_FLAVOR, ud)
            return
        if not (any(n["ready"] for n in masters) and master_ip):
            return
        if len(workers) < want:
            _spawn_workers(cluster, master_ip, len(workers), want)
            return
        if len(workers) == want and all(n["ready"] for n in nodes):
            _set_status(cid, "ACTIVE", "")

    elif status == "SCALING":
        if not master_ip:
            return
        if len(workers) < want:
            _spawn_workers(cluster, master_ip, len(workers), want)
        elif len(workers) > want:
            for w in workers[want:]:  # newest last
                _delete_vm(w["server_id"])
                _delete_node_record(cid, w["id"])
        elif all(n["ready"] for n in nodes):
            _set_status(cid, "ACTIVE", "")

def tick() -> None:
    try:
        r = requests.get(f"{API}/internal/k8s-engine/clusters", headers=internal_headers(), timeout=10)
        r.raise_for_status()
        clusters = r.json()
        for cls in clusters:
            try:
                reconcile_cluster(cls)
            except Exception as exc:  # best-effort: one cluster failing must not block the others
                log.error("reconcile failed for cluster %s: %s", cls.get("id"), exc)
                if cls.get("status") == "CREATING":
                    try:
                        _set_status(cls["id"], "ERROR", str(exc)[:200])
                        for n in cls.get("nodes", []):
                            _delete_vm(n["server_id"])
                    except Exception:  # best-effort: the cluster stays ERROR; deleting it retries every VM
                        pass
    except Exception as exc:  # best-effort: fetch clusters failure
        log.warning("failed to fetch clusters for reconciliation: %s", exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s  %(message)s")
    while True:
        try:
            tick()
        except Exception as exc:  # best-effort: main loop exception safety
            log.warning("tick failed: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
