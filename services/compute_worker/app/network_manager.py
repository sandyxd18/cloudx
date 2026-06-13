from .config import OVS_BRIDGE
from .utils import run, short_id


def tap_name(server_id: str) -> str:
    return f"tap-mini-{short_id(server_id, 6)}"


def port_tap_name(server_id: str, port_id: str) -> str:
    return f"tap-mini-{short_id(server_id, 3)}{short_id(port_id, 3)}"


def create_tap(name: str) -> None:
    delete_tap(name)
    created = run(["ip", "tuntap", "add", "dev", name, "mode", "tap"], check=False)
    if created.returncode != 0:
        message = created.stderr.strip() or created.stdout.strip()
        raise RuntimeError(
            f"failed to create tap interface {name}: {message}. "
            "Run compute-worker as privileged with NET_ADMIN, /dev/net/tun mounted, and host network mode."
        )
    verify = run(["ip", "link", "show", "dev", name], check=False)
    if verify.returncode != 0:
        message = verify.stderr.strip() or verify.stdout.strip()
        raise RuntimeError(
            f"tap interface {name} was created but is not visible: {message}. "
            "Check that compute-worker uses the host network namespace."
        )
    run(["ip", "link", "set", name, "up"])


def bind_tap_to_ovs(name: str, logical_port_name: str, mac_address: str, server_id: str) -> None:
    run(["ovs-vsctl", "--may-exist", "add-port", OVS_BRIDGE, name])
    run(["ovs-vsctl", "set", "Interface", name, f"external_ids:iface-id={logical_port_name}"])
    run(["ovs-vsctl", "set", "Interface", name, f"external_ids:attached-mac={mac_address}"])
    run(["ovs-vsctl", "set", "Interface", name, f"external_ids:vm-id={server_id}"])
    run(["ovs-vsctl", "set", "Interface", name, "external_ids:iface-status=active"])


def delete_tap(name: str) -> None:
    run(["ovs-vsctl", "--if-exists", "del-port", name], check=False)
    run(["ip", "link", "delete", name], check=False)
