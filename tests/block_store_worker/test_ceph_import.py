from types import SimpleNamespace

import pytest

from services.block_store_worker.app.drivers import ceph
from shared.proc import CommandError  # the module the driver imports (services/ is on the path)


@pytest.fixture
def cmds(monkeypatch):
    seen = []
    monkeypatch.setattr(ceph, "CEPH_POOL", "volumes")
    monkeypatch.setattr(ceph, "CEPH_CONF", "/etc/ceph/ceph.conf")
    monkeypatch.setattr(ceph, "CEPH_KEYRING", "/etc/ceph/ceph.client.cloudx.keyring")
    monkeypatch.setattr(ceph, "CEPH_USER", "cloudx")
    monkeypatch.setattr(ceph, "run", lambda cmd, **kw: seen.append(("run", list(cmd))))
    monkeypatch.setattr(ceph, "output", lambda cmd, **kw: seen.append(("output", list(cmd))) or "")
    return seen


def test_an_image_is_converted_to_raw_as_the_ceph_user_with_the_drivers_conf_and_keyring(cmds):
    path = ceph.create_from_source("vol-1", "/tmp/cirros.qcow2", 2)

    assert path == "rbd:volumes/vol-1"
    kind, cmd = cmds[0]
    assert cmd[:5] == ["qemu-img", "convert", "-O", "raw", "/tmp/cirros.qcow2"]
    assert cmd[5] == "rbd:volumes/vol-1:id=cloudx:conf=/etc/ceph/ceph.conf:keyring=/etc/ceph/ceph.client.cloudx.keyring"


def test_rbd_commands_run_as_the_ceph_user(cmds):
    ceph.delete("vol-1")
    assert cmds[0][1][:5] == ["rbd", "--conf", "/etc/ceph/ceph.conf", "--id", "cloudx"]


def test_a_failed_conversion_is_an_error_not_a_raw_import_of_the_qcow2_file(monkeypatch, cmds):
    def failing_run(cmd, check=True, **kw):  # like shared.proc.run: raises unless check=False
        if check:
            raise CommandError(list(cmd), 127)
        return SimpleNamespace(returncode=127, stderr="qemu-img: command not found")
    monkeypatch.setattr(ceph, "run", failing_run)

    with pytest.raises(CommandError, match="qemu-img: command not found"):
        ceph.create_from_source("vol-1", "/tmp/cirros.qcow2", 2)

    assert not any("import" in cmd for _, cmd in cmds), "importing the file as-is would store qcow2 bytes in a volume read as raw"
