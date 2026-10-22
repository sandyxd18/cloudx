import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_cleanup_scripts_dry_run():
    for script in ("cleanup-compute.sh", "cleanup-network.sh", "cleanup-all.sh", "cleanup-provider-network.sh"):
        result = subprocess.run(["bash", str(ROOT / "scripts" / script), "--dry-run"], text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr

def test_ovn_northd_start_creates_runtime_dir():
    script = ROOT / "services" / "ovn" / "start-northd.sh"
    content = script.read_text()
    assert "mkdir -p /var/run/ovn" in content
    assert "--pidfile=/var/run/ovn/ovn-northd.pid" in content


def test_prepare_lvm_node_dry_run_writes_the_per_node_worker_settings():
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "prepare-lvm-node.sh"), "--host", "node-a", "--portal-ip", "10.0.0.1", "--dry-run"],
        text=True, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "apt-get install -y -qq lvm2 targetcli-fb open-iscsi" in result.stdout
    assert "block-store-worker/block-store-worker.conf" in result.stdout


def test_prepare_lvm_node_needs_a_host_name_and_portal_address():
    result = subprocess.run(["bash", str(ROOT / "scripts" / "prepare-lvm-node.sh"), "--dry-run"], text=True, capture_output=True, timeout=20)
    assert result.returncode == 2 and "--host and --portal-ip are required" in result.stderr


def test_setup_compute_node_names_the_worker_after_the_node():
    script = (ROOT / "scripts" / "setup-compute-node.sh").read_text(encoding="utf-8")
    assert "compute_host = ${NODE_NAME}" in script


def test_setup_compute_node_writes_only_node_keys_and_auth_api_url():
    script = (ROOT / "scripts" / "setup-compute-node.sh").read_text(encoding="utf-8")
    block = script.split('<<EOF\n', 1)[1].split("\nEOF\n", 1)[0]
    keys = [line.split("=", 1)[0].strip() for line in block.splitlines() if "=" in line]
    assert keys == ["compute_host", "node_name", "host_ip", "rabbitmq_url", "database_url", "auth_api_url"]

def test_build_k3s_image_dry_run_bakes_k3s_and_uploads():
    result = subprocess.run(["bash", str(ROOT / "scripts" / "build-k3s-image.sh"), "--base-image", "u.img", "--dry-run"], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "k3s-airgap-images-amd64.tar.zst" in result.stdout and "v1.30.2%2Bk3s1" in result.stdout
    assert "cloudx image upload k3s-ubuntu" in result.stdout


def test_build_k3s_image_rejects_bad_input():
    base = ["bash", str(ROOT / "scripts" / "build-k3s-image.sh")]
    assert subprocess.run(base + ["--dry-run"], text=True, capture_output=True, timeout=20).returncode == 2
    bad = subprocess.run(base + ["--base-image", "u.img", "--k3s-version", "v1;rm -rf /", "--dry-run"], text=True, capture_output=True, timeout=20)
    assert bad.returncode == 2 and "invalid --k3s-version" in bad.stderr


def _libvirt_binding_block() -> str:
    script = (ROOT / "scripts" / "setup-compute-node.sh").read_text(encoding="utf-8")
    return script.split("# --- libvirt binding ---\n", 1)[1].split("# --- end libvirt binding ---", 1)[0]


def _run_libvirt_binding(tmp_path, *, venv: bool, importable: bool = False):
    python, calls = tmp_path / "python", tmp_path / "calls"
    if venv:
        python.write_text(f'#!/bin/sh\nif [ "$1" = "-c" ]; then exit {0 if importable else 1}; fi\necho "$@" >> {calls}\n')
        python.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "CLOUDX_VENV_PYTHON": str(python)}
    result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + _libvirt_binding_block()], env=env, text=True, capture_output=True)
    return result, (calls.read_text() if calls.exists() else "")


def test_setup_compute_node_installs_the_libvirt_binding_when_the_venv_lacks_it(tmp_path):
    result, calls = _run_libvirt_binding(tmp_path, venv=True, importable=False)
    assert result.returncode == 0, result.stderr
    assert calls == "-m pip install libvirt-python\n"


def test_setup_compute_node_leaves_a_venv_that_already_has_the_binding_alone(tmp_path):
    result, calls = _run_libvirt_binding(tmp_path, venv=True, importable=True)
    assert result.returncode == 0, result.stderr
    assert calls == ""


def test_setup_compute_node_without_a_venv_yet_says_what_to_run_later_and_does_not_fail(tmp_path):
    result, calls = _run_libvirt_binding(tmp_path, venv=False)
    assert result.returncode == 0, result.stderr
    assert calls == ""
    assert "pip install libvirt-python" in result.stdout


def _system_id_block() -> str:
    script = (ROOT / "scripts" / "setup-compute-node.sh").read_text(encoding="utf-8")
    return script.split("# --- system-id ---\n", 1)[1].split("# --- end system-id ---", 1)[0]


def _run_system_id(tmp_path, conf_text=None):
    # ovs-ctl rewrites external_ids:system-id from system-id.conf whenever Open vSwitch starts, so a name set only with
    # ovs-vsctl is lost on the next restart (an unattended upgrade is enough) and the chassis comes back under a random UUID.
    bin_dir, calls, conf = tmp_path / "bin", tmp_path / "calls", tmp_path / "system-id.conf"
    bin_dir.mkdir()
    (bin_dir / "ovs-vsctl").write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    (bin_dir / "ovs-vsctl").chmod(0o755)
    if conf_text is not None:
        conf.write_text(conf_text)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "NODE_NAME": "compute-x", "OVS_SYSTEM_ID_CONF": str(conf)}
    result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + _system_id_block()], env=env, text=True, capture_output=True)
    return result, conf, (calls.read_text() if calls.exists() else "")


def test_setup_compute_node_makes_the_node_name_survive_an_open_vswitch_restart(tmp_path):
    result, conf, calls = _run_system_id(tmp_path)
    assert result.returncode == 0, result.stderr
    assert conf.read_text() == "compute-x\n"
    assert "set open_vswitch . external_ids:system-id=compute-x" in calls


def test_setup_compute_node_replaces_the_random_system_id_ovs_generated(tmp_path):
    result, conf, _ = _run_system_id(tmp_path, conf_text="2380ccf4-ad7b-4c62-bbdc-0666655e073f\n")
    assert result.returncode == 0, result.stderr
    assert conf.read_text() == "compute-x\n"
