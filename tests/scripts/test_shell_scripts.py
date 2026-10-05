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
    assert "cloudx-block-store-worker.service.d/node.conf" in result.stdout


def test_prepare_lvm_node_needs_a_host_name_and_portal_address():
    result = subprocess.run(["bash", str(ROOT / "scripts" / "prepare-lvm-node.sh"), "--dry-run"], text=True, capture_output=True, timeout=20)
    assert result.returncode == 2 and "--host and --portal-ip are required" in result.stderr


def test_setup_compute_node_names_the_worker_after_the_node():
    script = (ROOT / "scripts" / "setup-compute-node.sh").read_text(encoding="utf-8")
    assert "Environment=COMPUTE_HOST=${NODE_NAME}" in script
