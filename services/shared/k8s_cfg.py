"""cloud-config for K3s master and worker VMs. Pure functions; every value is validated because it lands in a shell script."""
from __future__ import annotations

import re

_NAME = re.compile(r"[a-z0-9-]{1,63}")
_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_TOKEN = re.compile(r"[A-Za-z0-9_.~-]{8,128}")
_IP = re.compile(r"\d{1,3}(\.\d{1,3}){3}")
_URL = re.compile(r"https?://[A-Za-z0-9.:-]+")


def _ok(rx: re.Pattern, value: str, what: str) -> str:
    if not rx.fullmatch(value or ""):
        raise ValueError(f"invalid {what}")
    return value


def _callback(cluster_id: str, role: str, callback_url: str, internal_token: str) -> str:
    url = f"{callback_url.rstrip('/')}/internal/k8s-engine/clusters/{cluster_id}/ready"
    return (
        "      for i in $(seq 1 120); do\n"
        f"        curl -sf -X POST -H 'X-Internal-Token: {internal_token}' -H 'Content-Type: application/json' "
        f"-d \"{{\\\"role\\\":\\\"{role}\\\",\\\"ip\\\":\\\"$IP\\\",\\\"node_name\\\":\\\"$(hostname)\\\"}}\" {url} && break\n"
        "        sleep 5\n"
        "      done\n"
        "      wait  # the unit owns k3s: when this script exits systemd kills the whole cgroup\n"
    )


def render_master(cluster_id: str, name: str, join_token: str, admin_token: str, callback_url: str, internal_token: str) -> str:
    _ok(_ID, cluster_id, "cluster_id"); _ok(_NAME, name, "name")
    _ok(_TOKEN, join_token, "join_token"); _ok(_TOKEN, admin_token, "admin_token"); _ok(_TOKEN, internal_token, "internal_token")
    _ok(_URL, callback_url, "callback_url")
    return (
        "#cloud-config\n"
        "write_files:\n"
        "  - path: /etc/rancher/k3s/token-auth\n"
        "    permissions: '0600'\n"
        f"    content: \"{admin_token},admin,admin,system:masters\\n\"\n"
        "  - path: /usr/local/bin/cloudx-k3s-boot.sh\n"
        "    permissions: '0700'\n"
        "    content: |\n"
        "      #!/bin/bash\n"
        "      IP=$(hostname -I | awk '{print $1}')\n"
        f"      k3s server --token {join_token} --tls-san $IP --node-name {name}-master "
        "--kube-apiserver-arg token-auth-file=/etc/rancher/k3s/token-auth >/var/log/k3s.log 2>&1 &\n"
        "      for i in $(seq 1 120); do\n"
        "        curl -sk https://127.0.0.1:6443/readyz -H 'Authorization: Bearer " + admin_token + "' | grep -q ok && break\n"
        "        sleep 5\n"
        "      done\n"
        + _callback(cluster_id, "master", callback_url, internal_token)
        + "runcmd:\n  - [ systemd-run, --unit=cloudx-k3s-boot, --property=Delegate=yes, /usr/local/bin/cloudx-k3s-boot.sh ]\n"
    )


def render_worker(cluster_id: str, master_ip: str, join_token: str, callback_url: str, internal_token: str) -> str:
    _ok(_ID, cluster_id, "cluster_id"); _ok(_IP, master_ip, "master_ip")
    _ok(_TOKEN, join_token, "join_token"); _ok(_TOKEN, internal_token, "internal_token"); _ok(_URL, callback_url, "callback_url")
    return (
        "#cloud-config\n"
        "write_files:\n"
        "  - path: /usr/local/bin/cloudx-k3s-boot.sh\n"
        "    permissions: '0700'\n"
        "    content: |\n"
        "      #!/bin/bash\n"
        "      IP=$(hostname -I | awk '{print $1}')\n"
        f"      k3s agent --server https://{master_ip}:6443 --token {join_token} >/var/log/k3s.log 2>&1 &\n"
        "      sleep 20\n"
        + _callback(cluster_id, "worker", callback_url, internal_token)
        + "runcmd:\n  - [ systemd-run, --unit=cloudx-k3s-boot, --property=Delegate=yes, /usr/local/bin/cloudx-k3s-boot.sh ]\n"
    )
