import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi import HTTPException
from shared import proc
from shared.proc import CommandError

ROOT = Path(__file__).resolve().parents[2]

def fake_run(monkeypatch, rc=0, stdout="", stderr="", raises=None):
    """Replace the single place where commands really execute; returns the list of (argv, kwargs) seen."""
    seen = []

    def _run(argv, **kwargs):
        seen.append((list(argv), kwargs))
        if raises:
            raise raises
        return subprocess.CompletedProcess(argv, rc, stdout, stderr)

    monkeypatch.setattr(proc.subprocess, "run", _run)
    return seen

def test_lvm_exists_is_unknown_not_false_when_lvdisplay_cannot_run(monkeypatch):
    from services.block_store_worker.app import reconciler
    from services.block_store_worker.app.drivers import lvm

    monkeypatch.setattr(lvm, "LVM_VG_NAME", "vg-test")
    fake_run(monkeypatch, raises=FileNotFoundError("lvdisplay"))
    with pytest.raises(CommandError, match="command not found"):
        lvm.exists("vol-1")
    # ...and the reconciler therefore leaves the volume alone instead of marking it missing
    assert reconciler._backend_exists_for_volume({"id": "1", "backend": "lvm"}) is None

def test_lvm_exists_is_false_when_lvdisplay_says_no(monkeypatch):
    from services.block_store_worker.app.drivers import lvm

    monkeypatch.setattr(lvm, "LVM_VG_NAME", "vg-test")
    seen = fake_run(monkeypatch, rc=5, stderr="Failed to find logical volume")
    assert lvm.exists("vol-1") is False
    assert seen[0][0] == ["lvdisplay", "vg-test/vol-1"]
    assert seen[0][1]["timeout"] == 120  # no command runs without a time limit

def test_lvm_failure_surfaces_the_tool_error(monkeypatch):
    from services.block_store_worker.app.drivers import lvm

    monkeypatch.setattr(lvm, "LVM_VG_NAME", "vg-test")
    fake_run(monkeypatch, rc=5, stderr="Volume group vg-test not found")
    with pytest.raises(RuntimeError, match="lvremove failed .*Volume group vg-test not found"):
        lvm.delete("vol-1")

def test_ceph_only_a_missing_image_means_not_exists(monkeypatch):
    from services.block_store_worker.app.drivers import ceph

    def missing(*a, **k):
        raise CommandError(["rbd"], 2, stderr="rbd: error opening image x: (2) No such file or directory")

    def cluster_down(*a, **k):
        raise CommandError(["rbd"], 110, stderr="monclient: authenticate timed out")

    monkeypatch.setattr(ceph, "_rbd", missing)
    assert ceph.exists("x") is False
    monkeypatch.setattr(ceph, "_rbd", cluster_down)
    with pytest.raises(CommandError):
        ceph.exists("x")

def test_nfs_driver_passes_argument_lists_with_time_limits(monkeypatch, tmp_path):
    from services.block_store_worker.app.drivers import nfs

    monkeypatch.setattr(nfs, "STORAGE_NFS_DIR", str(tmp_path))
    seen = fake_run(monkeypatch)
    nfs.create("vol-a", 5)
    argv, kwargs = seen[0]
    assert argv == ["qemu-img", "create", "-f", "qcow2", str(tmp_path / "vol-a.qcow2"), "5G"]
    assert kwargs["timeout"] == 120
    seen.clear()
    nfs.clone("/src.qcow2", "snap", "vol-b")
    assert seen[0][1]["timeout"] is None  # copying a whole image: unlimited, and explicit about it
    fake_run(monkeypatch, rc=1, stderr="Could not open")
    with pytest.raises(RuntimeError, match="qemu-img failed"):
        nfs.create("vol-c", 1)

def test_minio_secret_never_appears_on_a_command_line_or_in_errors(monkeypatch):
    from services.block_store_worker.app import main as worker

    monkeypatch.setattr(worker, "MINIO_ENDPOINT", "minio.lab:9000")
    monkeypatch.setattr(worker, "MINIO_ACCESS_KEY", "ak")
    monkeypatch.setattr(worker, "MINIO_SECRET_KEY", "s3/cr:et@!")
    seen = fake_run(monkeypatch, rc=1, stderr="mc: <ERROR> unable to connect with s3/cr:et@!")
    with pytest.raises(CommandError) as exc:
        worker._mc(["cp", "/tmp/x", "cloudx/backups/obj"])
    argv, kwargs = seen[0]
    assert argv[:2] == ["mc", "cp"] and not any("s3/cr" in a for a in argv)
    assert kwargs["env"]["MC_HOST_cloudx"] == "http://ak:s3%2Fcr%3Aet%40%21@minio.lab:9000"
    assert "s3/cr:et@!" not in str(exc.value) and "s3%2Fcr" not in str(exc.value)

def test_ovn_helper_maps_command_failures_to_http_errors(monkeypatch):
    from services.networking.app import ovn_cli

    seen = fake_run(monkeypatch, stdout="  ok  \n")
    assert ovn_cli.ovn("show") == "ok"
    assert seen[0][0][0] == "ovn-nbctl" and seen[0][1]["timeout"] == ovn_cli.OVN_TIMEOUT
    fake_run(monkeypatch, rc=1, stderr="connection refused")
    with pytest.raises(HTTPException) as exc:
        ovn_cli.ovn("show")
    assert exc.value.status_code == 502 and "connection refused" in str(exc.value.detail)
    fake_run(monkeypatch, raises=FileNotFoundError("ovn-nbctl"))   # used to be an unhandled FileNotFoundError (500)
    with pytest.raises(HTTPException) as exc:
        ovn_cli.ovn("show")
    assert exc.value.status_code == 502
    assert ovn_cli.ovn("show", check=False) == ""

def test_console_log_uses_time_limited_commands(monkeypatch, tmp_path):
    from services.compute_worker.app import libvirt_driver

    log_file = tmp_path / "console.log"
    log_file.write_text("boot ok\n")
    xml = f"<domain><devices><console type='pty'><log file='{log_file}'/></console></devices></domain>"
    calls = []

    def _run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, xml if argv[0] == "virsh" else "boot ok\n", "")

    monkeypatch.setattr(proc.subprocess, "run", _run)
    assert libvirt_driver.get_console_log("vm-1", 20).strip() == "boot ok"
    assert [c[0][0] for c in calls] == ["virsh", "tail"] and all(c[1]["timeout"] for c in calls)

def test_cli_service_commands_do_not_go_through_a_shell(monkeypatch):
    import sys

    sys.path.insert(0, str(ROOT / "cli"))
    from cloudx_cli.main import cli

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    runner = CliRunner()

    ok = runner.invoke(cli, ["service", "restart", "compute-api"])
    assert ok.exit_code == 0 and calls == [["sudo", "systemctl", "restart", "cloudx-compute-api"]]

    calls.clear()
    evil = runner.invoke(cli, ["service", "restart", "x; touch /tmp/pwned"])
    assert evil.exit_code == 2 and "invalid service name" in evil.output and calls == []

    runner.invoke(cli, ["service", "logs", "auth-api", "-n", "5"])
    assert calls[-1] == ["journalctl", "-u", "cloudx-auth-api", "-n", "5", "--no-pager"]

def test_cli_never_uses_os_system_or_shell_true():
    for path in (ROOT / "cli").rglob("*.py"):
        if "build" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        assert "os.system(" not in src and "shell=True" not in src, path
