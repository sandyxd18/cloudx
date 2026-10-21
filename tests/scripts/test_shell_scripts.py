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
