"""Render BIND zone files and named.conf for the two CloudX processes (auth = public authoritative, resolver = tenant recursive).

Inputs are validated by shared.dns_validate; the renderer still refuses anything that could break out of a conf/zone line.
"""
import ipaddress
import re

from shared.dns_validate import LABEL

_PORT = 53


def _zone_name(n: str) -> str:
    if not n or not all(LABEL.match(x) for x in n.split(".")):
        raise ValueError(f"unsafe zone name {n[:40]!r}")
    return n


def _addr(a: str) -> str:
    return str(ipaddress.ip_address(a))


def _net(a: str) -> str:
    return str(ipaddress.ip_network(a, strict=False))


def hostmaster_from_email(email: str) -> str:
    local, _, dom = (email or "hostmaster@localhost").partition("@")
    return f"{local.replace('.', chr(92) + '.')}.{dom.rstrip('.')}."


def _txt(v: str) -> str:
    esc = v.replace("\\", "\\\\").replace('"', '\\"')
    chunks = [esc[i:i + 200] for i in range(0, len(esc), 200)]  # <=255 bytes each even after escaping short runs
    return " ".join(f'"{c}"' for c in chunks)


def render_zone(zone: dict, recordsets: list[dict], ns_fqdn: str, hostmaster: str) -> str:
    ttl = zone["ttl"]
    lines = [f"$ORIGIN {zone['name']}.", f"$TTL {ttl}",
             f"@ IN SOA {ns_fqdn} {hostmaster} {zone['serial']} 3600 600 1209600 {ttl}",
             f"@ IN NS {ns_fqdn}"]
    for rs in recordsets:
        t = f" {rs['ttl']}" if rs.get("ttl") else ""
        for data in rs["records"]:
            val = _txt(data) if rs["type"] == "TXT" else data
            lines.append(f"{rs['name']}{t} IN {rs['type']} {val}")
    return "\n".join(lines) + "\n"


def render_named_conf(role: str, zones: list[dict], cfg: dict) -> str:
    """role 'auth' or 'resolver'. cfg: root, address, control_port, forwarders, clients."""
    auth = role == "auth"
    root, addr, port = cfg["root"], _addr(cfg["address"]), int(cfg["control_port"])
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", root):
        raise ValueError("unsafe root path")
    out = [f'include "{root}/rndc.key";',
           f"controls {{ inet 127.0.0.1 port {port} allow {{ 127.0.0.1; }} keys {{ \"rndc-key\"; }}; }};"]
    if not auth:
        nets = "; ".join(_net(c) for c in ["127.0.0.0/8", *cfg.get("clients", [])])
        out.append(f"acl clients {{ {nets}; }};")
    opts = [f'directory "{root}";', f"listen-on port {_PORT} {{ {addr}; }};", "listen-on-v6 { none; };",
            f'pid-file "{root}/named.pid";', f'session-keyfile "{root}/session.key";', "allow-transfer { none; };"]
    if auth:
        opts += ["recursion no;", "allow-query { any; };", "version none;"]
    else:
        fw = "; ".join(_addr(f) for f in cfg.get("forwarders", []))
        opts += ["recursion yes;", "allow-recursion { clients; };", "allow-query { clients; };", "dnssec-validation no;"]  # forwarders strip DNSSEC; CloudX has no DNSSEC
        if fw:
            opts += [f"forwarders {{ {fw}; }};", "forward only;"]
    out.append("options {\n    " + "\n    ".join(opts) + "\n};")
    for z in zones:
        if auth and z["scope"] == "internal":
            continue  # private addresses never leave through the public server
        name = _zone_name(z["name"])
        body = [f'type primary;', f'file "{root}/zones/{name}.zone";']
        sec = [_addr(s) for s in z.get("secondaries", [])]
        if auth and sec:
            joined = "; ".join(sec)
            body += [f"allow-transfer {{ {joined}; }};", f"also-notify {{ {joined}; }};", "notify explicit;"]
        else:
            body += ["allow-transfer { none; };", "notify no;"]
        out.append(f'zone "{name}" {{ ' + " ".join(body) + " };")
    return "\n".join(out) + "\n"
