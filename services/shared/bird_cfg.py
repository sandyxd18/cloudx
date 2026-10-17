"""bird.conf configuration renderer (pure function) for BGP Dynamic Routing"""
from __future__ import annotations
import ipaddress


def render(speaker: dict, peers: list[dict], mask_password: bool = False) -> str:
    """
    Render BIRD 2 configuration string for a BGPSpeaker and its active BGP peers.

    speaker dict keys:
        outer_ip: str
        router_ext_ip: str
        local_subnets: list[str]
    peer dict keys:
        id: str
        name: str
        peer_ip: str
        remote_as: int
        local_as: int
        auth_password: str | None
        advertised_prefixes: list[str] | None
    """
    # ── 1. Validate speaker inputs ──
    outer_ip_str = speaker.get("outer_ip")
    if not outer_ip_str:
        raise ValueError("speaker outer_ip is required")
    try:
        outer_ip_obj = ipaddress.ip_address(outer_ip_str)
        if outer_ip_obj.version != 4:
            raise ValueError("outer_ip must be IPv4")
    except Exception as e:
        if isinstance(e, ValueError) and "must be IPv4" in str(e):
            raise
        raise ValueError(f"Invalid outer_ip: {outer_ip_str}") from e

    router_ext_ip_str = speaker.get("router_ext_ip")
    if not router_ext_ip_str:
        raise ValueError("speaker router_ext_ip is required")
    try:
        router_ext_ip_obj = ipaddress.ip_address(router_ext_ip_str)
        if router_ext_ip_obj.version != 4:
            raise ValueError("router_ext_ip must be IPv4")
    except Exception as e:
        if isinstance(e, ValueError) and "must be IPv4" in str(e):
            raise
        raise ValueError(f"Invalid router_ext_ip: {router_ext_ip_str}") from e

    local_subnets = speaker.get("local_subnets", [])
    validated_local_subnets = []
    for subnet_str in local_subnets:
        try:
            net = ipaddress.ip_network(subnet_str, strict=False)
            if net.version != 4:
                raise ValueError("local_subnets must be IPv4")
            validated_local_subnets.append(net)
        except Exception as e:
            if isinstance(e, ValueError):
                raise
            raise ValueError(f"Invalid local subnet: {subnet_str}") from e

    # ── 2. Collect static routes (advertised prefixes) ──
    static_routes_block = []
    all_advertised_prefixes = set()

    for peer in peers:
        if not peer.get("is_active", True):
            continue
        prefixes = peer.get("advertised_prefixes", [])
        if isinstance(prefixes, str):
            import json
            try:
                prefixes = json.loads(prefixes)
            except Exception:  # best-effort: fallback to literal string list if json parsing fails
                prefixes = [prefixes]
        for pfx in prefixes:
            if not pfx:
                continue
            try:
                net = ipaddress.ip_network(pfx, strict=False)
                if net.version != 4:
                    raise ValueError("advertised prefix must be IPv4")
                all_advertised_prefixes.add(str(net))
            except Exception as e:
                if isinstance(e, ValueError):
                    raise
                raise ValueError(f"Invalid advertised prefix: {pfx}") from e

    for i, pfx in enumerate(sorted(all_advertised_prefixes)):
        static_routes_block.append(f"    route {pfx} blackhole;")

    static_routes_str = "\n".join(static_routes_block)

    # ── 3. Render peers ──
    peer_blocks = []
    for peer in peers:
        if not peer.get("is_active", True):
            continue

        pid = peer.get("id", "peer")
        safe_pid = "".join(c if c.isalnum() or c == "_" else "_" for c in pid)
        proto_name = f"bgp_{safe_pid}"

        peer_ip = peer.get("peer_ip")
        if not peer_ip:
            raise ValueError(f"Peer {pid} missing peer_ip")
        try:
            p_ip_obj = ipaddress.ip_address(peer_ip)
            if p_ip_obj.version != 4:
                raise ValueError("peer_ip must be IPv4")
        except Exception as e:
            if isinstance(e, ValueError) and "must be IPv4" in str(e):
                raise
            raise ValueError(f"Invalid peer_ip: {peer_ip}") from e

        try:
            remote_as = int(peer.get("remote_as", 0))
            if not (1 <= remote_as <= 4294967295):
                raise ValueError()
        except Exception:
            raise ValueError(f"Invalid remote_as: {peer.get('remote_as')}")

        try:
            local_as = int(peer.get("local_as", 0))
            if not (1 <= local_as <= 4294967295):
                raise ValueError()
        except Exception:
            raise ValueError(f"Invalid local_as: {peer.get('local_as')}")

        password = peer.get("auth_password")
        password_str = ""
        if password:
            if any(ch in password for ch in ('"', '\\', '\n', '\r', '\t')) or any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
                raise ValueError("BGP password contains forbidden characters")
            display_pwd = "***" if mask_password else password
            password_str = f'    password "{display_pwd}";\n'

        # Import filter logic: reject 0.0.0.0/0 and prefix tumpang tindih local_subnets
        # (same subnet, more specific, or supernet)
        # We can encode this in BIRD filter or accept all and handle import in filter logic.
        # BIRD filter syntax for rejecting 0.0.0.0/0 and local subnet overlaps:
        # if net = 0.0.0.0/0 then reject;
        # plus check overlap with each local subnet:
        # local subnet check in BIRD or explicit filter conditions.
        # Let's construct filter statements for local subnet overlaps.
        # If a learned route overlaps with any local subnet (net subnetof local or local subnetof net or net = local), reject.
        overlap_checks = []
        for lnet in validated_local_subnets:
            # In BIRD filter, subnetof or eq:
            # We can check: if net ~ <lnet> or <lnet> ~ net then reject;
            overlap_checks.append(f"if net ~ [ {lnet}+, {lnet}- ] then reject;")

        overlap_filter_code = "\n        ".join(overlap_checks)

        peer_blocks.append(f"""protocol bgp {proto_name} {{
    local {outer_ip_str} as {local_as};
    neighbor {peer_ip} as {remote_as};
{password_str}    ipv4 {{
        import filter {{
            if net = 0.0.0.0/0 then reject;
            {overlap_filter_code}
            accept;
        }};
        export filter {{
            if proto = "static4" then {{ bgp_next_hop = {router_ext_ip_str}; accept; }}
            reject;
        }};
    }};
}}""")

    peers_str = "\n\n".join(peer_blocks)

    return f"""router id {outer_ip_str};

protocol device {{
}}

protocol static static4 {{
    ipv4;
{static_routes_str}
}}

{peers_str}
"""
