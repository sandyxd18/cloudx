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


def _script_block(name: str) -> str:
    script = (ROOT / "scripts" / "setup-compute-node.sh").read_text(encoding="utf-8")
    return script.split(f"# --- {name} ---\n", 1)[1].split(f"# --- end {name} ---", 1)[0]


def _run_compute_blocks(tmp_path, blocks, *, old_system_id="", mappings="", sbctl_fails=False, extra_env=None):
    """Run blocks of setup-compute-node.sh against fake ovs-vsctl/ovn-sbctl that log every call."""
    bin_dir, calls = tmp_path / "bin", tmp_path / "calls"
    bin_dir.mkdir()
    fake = (
        '#!/bin/sh\n'
        'echo "$(basename $0) $@" >> "$FAKE_CALLS"\n'
        'case "$*" in\n'
        '  *"get open_vswitch . external_ids:system-id"*) echo "\\"$FAKE_OLD_SYSTEM_ID\\"" ;;\n'
        '  *"get open_vswitch . external_ids:ovn-bridge-mappings"*) [ -n "$FAKE_MAPPINGS" ] && echo "\\"$FAKE_MAPPINGS\\"" ;;\n'
        'esac\n'
        '[ "$(basename $0)" = ovn-sbctl ] && [ -n "$FAKE_SBCTL_FAILS" ] && exit 1\n'
        'exit 0\n'
    )
    for tool in ("ovs-vsctl", "ovn-sbctl"):
        (bin_dir / tool).write_text(fake)
        (bin_dir / tool).chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "NODE_NAME": "compute-x", "CONTROLLER_IP": "10.0.0.5",
           "OVS_SYSTEM_ID_CONF": str(tmp_path / "system-id.conf"), "FAKE_CALLS": str(calls),
           "FAKE_OLD_SYSTEM_ID": old_system_id, "FAKE_MAPPINGS": mappings,
           "FAKE_SBCTL_FAILS": "1" if sbctl_fails else "", **(extra_env or {})}
    body = "set -euo pipefail\n" + "".join(_script_block(b) for b in blocks)
    result = subprocess.run(["bash", "-c", body], env=env, text=True, capture_output=True)
    return result, (calls.read_text() if calls.exists() else "")


def test_setup_compute_node_deletes_the_chassis_the_node_registered_under_its_old_name(tmp_path):
    # ovn-central brings an ovn-controller that registers under a random UUID. After the rename OVN refuses the new
    # chassis ("already has encap ip ... cannot duplicate") until the old row is gone.
    result, calls = _run_compute_blocks(tmp_path, ["system-id", "stale chassis"], old_system_id="8211e5f1-old")
    assert result.returncode == 0, result.stderr
    assert "ovn-sbctl --db=tcp:10.0.0.5:6642" in calls and "chassis-del 8211e5f1-old" in calls


def test_setup_compute_node_leaves_the_chassis_alone_when_the_name_did_not_change(tmp_path):
    result, calls = _run_compute_blocks(tmp_path, ["system-id", "stale chassis"], old_system_id="compute-x")
    assert result.returncode == 0, result.stderr
    assert "chassis-del" not in calls


def test_setup_compute_node_without_a_previous_system_id_deletes_nothing(tmp_path):
    result, calls = _run_compute_blocks(tmp_path, ["system-id", "stale chassis"], old_system_id="")
    assert result.returncode == 0, result.stderr
    assert "chassis-del" not in calls


def test_setup_compute_node_goes_on_when_the_old_chassis_cannot_be_deleted(tmp_path):
    result, calls = _run_compute_blocks(tmp_path, ["system-id", "stale chassis"], old_system_id="8211e5f1-old",
                                        sbctl_fails=True)
    assert result.returncode == 0, result.stderr
    assert "chassis-del 8211e5f1-old" in calls
    assert "could not delete" in result.stdout + result.stderr


def test_setup_compute_node_maps_the_provider_network_to_br_ex(tmp_path):
    # networking-api tries this itself but cannot write to the Open vSwitch socket as the unprivileged cloudx user
    result, calls = _run_compute_blocks(tmp_path, ["bridge mapping"])
    assert result.returncode == 0, result.stderr
    assert "set open_vswitch . external_ids:ovn-bridge-mappings=physnet1:br-ex" in calls


def test_setup_compute_node_keeps_other_bridge_mappings(tmp_path):
    result, calls = _run_compute_blocks(tmp_path, ["bridge mapping"], mappings="physnet2:br-x")
    assert result.returncode == 0, result.stderr
    assert "external_ids:ovn-bridge-mappings=physnet2:br-x,physnet1:br-ex" in calls


def test_setup_compute_node_does_not_repeat_a_mapping_that_is_already_there(tmp_path):
    result, calls = _run_compute_blocks(tmp_path, ["bridge mapping"], mappings="physnet1:br-ex")
    assert result.returncode == 0, result.stderr
    assert "set open_vswitch" not in calls


def test_setup_compute_node_uses_the_provider_physnet_from_the_environment(tmp_path):
    result, calls = _run_compute_blocks(tmp_path, ["bridge mapping"], extra_env={"PROVIDER_PHYSNET": "external"})
    assert result.returncode == 0, result.stderr
    assert "external_ids:ovn-bridge-mappings=external:br-ex" in calls


def _local_api_block() -> str:
    script = (ROOT / "scripts" / "prepare-lvm-node.sh").read_text(encoding="utf-8")
    return script.split("# --- local api ---\n", 1)[1].split("# --- end local api ---", 1)[0]


def _run_local_api(tmp_path, conf_text, host="cloudx-compute-01"):
    # one machine running both block-store-api and block-store-worker (the all-in-one install) lists itself in
    # block_store_lvm_hosts, otherwise block-store-api answers "No volume types"/never picks an LVM host
    conf_dir = tmp_path / "block-store-api"
    conf_dir.mkdir()
    conf = conf_dir / "block-store-api.conf"
    if conf_text is not None:
        conf.write_text(conf_text)
    env = {"PATH": os.environ["PATH"], "HOST_NAME": host, "CLOUDX_CONF_DIR": str(tmp_path), "DRY": "0", "LOCAL_API": "1"}
    body = 'set -euo pipefail\nact() { "$@"; }\n' + _local_api_block()
    result = subprocess.run(["bash", "-c", body], env=env, text=True, capture_output=True)
    return result, (conf.read_text() if conf.exists() else None)


SAMPLE_API_CONF = (
    "# Overrides for block-store-api only.\n[DEFAULT]\n"
    "# LVM volumes live on one node each.\n# block_store_lvm_hosts = cloudx-compute-01,cloudx-storage-01\n"
)


def test_setup_storage_local_api_turns_the_commented_sample_into_the_host_list(tmp_path):
    result, conf = _run_local_api(tmp_path, SAMPLE_API_CONF)
    assert result.returncode == 0, result.stderr
    assert "\nblock_store_lvm_hosts = cloudx-compute-01\n" in conf
    assert "# block_store_lvm_hosts = cloudx-compute-01,cloudx-storage-01" not in conf


def test_setup_storage_local_api_appends_to_an_existing_host_list(tmp_path):
    result, conf = _run_local_api(tmp_path, "[DEFAULT]\nblock_store_lvm_hosts = node-a\n", host="node-b")
    assert result.returncode == 0, result.stderr
    assert conf.count("block_store_lvm_hosts") == 1 and "block_store_lvm_hosts = node-a,node-b\n" in conf


def test_setup_storage_local_api_does_not_list_a_host_twice(tmp_path):
    result, conf = _run_local_api(tmp_path, "[DEFAULT]\nblock_store_lvm_hosts = node-a,node-b\n", host="node-b")
    assert result.returncode == 0, result.stderr
    assert "block_store_lvm_hosts = node-a,node-b\n" in conf and "node-b,node-b" not in conf


def test_setup_storage_local_api_without_any_line_adds_one(tmp_path):
    result, conf = _run_local_api(tmp_path, "[DEFAULT]\nlog_level = info\n")
    assert result.returncode == 0, result.stderr
    assert "block_store_lvm_hosts = cloudx-compute-01" in conf and "log_level = info" in conf


def test_setup_storage_local_api_needs_a_block_store_api_here(tmp_path):
    result, _ = _run_local_api(tmp_path, None)
    assert result.returncode != 0
    assert "block-store-api" in result.stderr


def test_setup_storage_dry_run_with_local_api_only_prints_the_plan():
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "prepare-lvm-node.sh"), "--host", "node-a", "--portal-ip", "10.0.0.1",
         "--local-api", "--dry-run"], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "block_store_lvm_hosts" in result.stdout


def test_setup_storage_rejects_a_host_name_that_would_end_up_in_sed_and_the_config():
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "prepare-lvm-node.sh"), "--host", "a|b", "--portal-ip", "10.0.0.1", "--dry-run"],
        text=True, capture_output=True, timeout=20)
    assert result.returncode == 2 and "invalid host name" in result.stderr
