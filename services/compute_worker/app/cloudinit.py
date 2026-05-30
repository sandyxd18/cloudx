from pathlib import Path
import ipaddress

from .utils import run, tool_available


def render_meta_data(server_id: str, server_name: str) -> str:
    return f"instance-id: {server_id}\nlocal-hostname: {server_name}\n"


def render_user_data(public_key: str | None = None) -> str:
    lines = [
        "#cloud-config",
        "package_update: false",
        "ssh_pwauth: false",
        "disable_root: true",
        "growpart:",
        "  mode: auto",
        "  devices: ['/']",
        "resize_rootfs: true",
    ]
    if public_key:
        lines.extend(["ssh_authorized_keys:", f"  - {public_key}"])
    return "\n".join(lines) + "\n"


def render_network_config(port: dict | None = None) -> str:
    if not port or not (port.get("fixed_ip") or port.get("ip_address")):
        return "version: 2\nethernets:\n  eth0:\n    dhcp4: true\n"
    fixed_ip = port.get("fixed_ip") or port.get("ip_address")
    prefix = ipaddress.ip_network(port.get("cidr", "0.0.0.0/24"), strict=False).prefixlen
    gateway = port.get("gateway_ip")
    dns = port.get("dns_nameservers") or []
    lines = [
        "version: 2",
        "ethernets:",
        "  eth0:",
        "    dhcp4: false",
        f"    addresses: [{fixed_ip}/{prefix}]",
    ]
    if gateway:
        lines.append(f"    gateway4: {gateway}")
    if dns:
        lines.extend(["    nameservers:", f"      addresses: [{', '.join(dns)}]"])
    return "\n".join(lines) + "\n"


def create_config_drive(instance_dir: Path, server_id: str, server_name: str, public_key: str | None, port: dict | None = None, user_data_content: str | None = None) -> Path:
    seed_dir = instance_dir / "cloud-init"
    seed_dir.mkdir(parents=True, exist_ok=True)
    meta_data = seed_dir / "meta-data"
    user_data = seed_dir / "user-data"
    network_config = seed_dir / "network-config"
    meta_data.write_text(render_meta_data(server_id, server_name), encoding="utf-8")
    user_data.write_text(user_data_content or render_user_data(public_key), encoding="utf-8")
    network_config.write_text(render_network_config(port), encoding="utf-8")
    seed_iso = instance_dir / "seed.iso"
    if tool_available("cloud-localds"):
        run(["cloud-localds", "-N", str(network_config), str(seed_iso), str(user_data), str(meta_data)])
    elif tool_available("genisoimage"):
        run(
            [
                "genisoimage",
                "-output",
                str(seed_iso),
                "-volid",
                "cidata",
                "-joliet",
                "-rock",
                "-graft-points",
                f"user-data={user_data}",
                f"meta-data={meta_data}",
                f"network-config={network_config}",
            ]
        )
    else:
        raise RuntimeError("cloud-localds or genisoimage is required to create NoCloud config-drive")
    return seed_iso
