import json
from pathlib import Path
import ipaddress

from .utils import run, tool_available


def render_meta_data(server_id: str, server_name: str, public_key: str | None = None, key_name: str | None = None) -> str:
    data = {
        "instance-id": server_id,
        "local-hostname": server_name,
    }
    if public_key:
        kn = key_name or "0"
        pk = public_key.strip()
        data["public-keys"] = {kn: pk, "0": pk}
        data["public_keys"] = {kn: pk, "0": pk}
    return json.dumps(data, indent=2) + "\n"


def render_user_data(public_key: str | None = None) -> str:
    lines = [
        "#cloud-config",
        "package_update: false",
        "growpart:",
        "  mode: auto",
        "  devices: ['/']",
        "resize_rootfs: true",
    ]
    if public_key:
        lines.extend([
            "ssh_authorized_keys:",
            f"  - {public_key.strip()}",
            "users:",
            "  - name: cirros",
            "    ssh_authorized_keys:",
            f"      - {public_key.strip()}",
        ])
    return "\n".join(lines) + "\n"


def render_network_config(port: dict | None = None) -> str:
    ipv4 = (port or {}).get("fixed_ip") or (port or {}).get("ip_address")
    ipv6 = (port or {}).get("ipv6_address")
    if not port or not (ipv4 or ipv6):
        return "version: 2\nethernets:\n  eth0:\n    dhcp4: true\n"
    mac = port.get("mac_address")  # Ubuntu renames eth0 to ens3, so match the NIC by MAC
    addresses, dns = [], list(port.get("dns_nameservers") or [])
    lines = [
        "version: 2",
        "ethernets:",
        "  nic0:",
        *([f"    match: {{macaddress: \"{mac.lower()}\"}}", "    set-name: eth0"] if mac else []),
        "    dhcp4: false",  # also for an IPv6-only port: waiting for DHCPv4 there only delays the boot
    ]
    if ipv4:
        addresses.append(f"{ipv4}/{ipaddress.ip_network(port.get('cidr', '0.0.0.0/24'), strict=False).prefixlen}")
    if ipv6:
        # the address and prefix are static, like the IPv4 ones: no RA or DHCPv6 is needed to get them
        addresses.append(f"{ipv6}/{ipaddress.ip_network(port.get('ipv6_cidr') or '::/64', strict=False).prefixlen}")
        dns += [d for d in port.get("ipv6_dns_nameservers") or [] if d not in dns]
    lines.append(f"    addresses: [{', '.join(addresses)}]")
    if ipv4 and port.get("gateway_ip"):
        lines.append(f"    gateway4: {port['gateway_ip']}")
    if ipv6 and port.get("ipv6_gateway_ip"):
        lines.append(f"    gateway6: {port['ipv6_gateway_ip']}")
    if dns:
        lines.extend(["    nameservers:", f"      addresses: [{', '.join(dns)}]"])
    return "\n".join(lines) + "\n"


def create_config_drive(instance_dir: Path, server_id: str, server_name: str, public_key: str | None, port: dict | None = None, user_data_content: str | None = None, key_name: str | None = None) -> Path:
    seed_dir = instance_dir / "cloud-init"
    seed_dir.mkdir(parents=True, exist_ok=True)
    meta_data = seed_dir / "meta-data"
    user_data = seed_dir / "user-data"
    network_config = seed_dir / "network-config"
    meta_data.write_text(render_meta_data(server_id, server_name, public_key, key_name), encoding="utf-8")
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
