"""swanctl.conf configuration renderer (pure function)"""

def render(gateway: dict, connections: list[dict], mask_psk: bool = False) -> str:
    """
    Render swanctl.conf configuration string for a VPNGateway and its active site connections.
    
    gateway dict keys:
        outer_ip: str
    connection dict keys:
        id: str
        name: str
        peer_address: str
        peer_id: str
        peer_cidrs: list[str]
        local_subnets: list[str]
        psk: str
        ike_auth: str
        ike_enc: str
        ike_pfs: str
        ipsec_auth: str
        ipsec_enc: str
    """
    # Map algorithm names to strongSwan names
    algo_map = {
        "aes-256": "aes256",
        "aes-128": "aes128",
        "sha256": "sha256",
        "sha1": "sha1",
        "sha384": "sha384",
        "sha512": "sha512",
        "group14": "modp2048",
        "group5": "modp1536",
        "group2": "modp1024",
        "group15": "modp3072",
        "group16": "modp4096",
    }

    outer_ip = gateway.get("outer_ip", "0.0.0.0")
    
    conns_block = []
    secrets_block = []

    for conn in connections:
        c_name = conn.get("name", conn.get("id", "conn"))
        peer_addr = conn.get("peer_address", "0.0.0.0")
        peer_id = conn.get("peer_id", peer_addr)
        
        peer_cidrs = conn.get("peer_cidrs", [])
        if isinstance(peer_cidrs, str):
            import json
            try:
                peer_cidrs = json.loads(peer_cidrs)
            except Exception: # best-effort: fallback to literal string list if json parsing fails
                peer_cidrs = [peer_cidrs]
        
        local_subnets = conn.get("local_subnets", [])
        
        ike_enc = algo_map.get(conn.get("ike_enc", "aes-256"), "aes256")
        ike_auth = algo_map.get(conn.get("ike_auth", "sha256"), "sha256")
        ike_pfs = algo_map.get(conn.get("ike_pfs", "group14"), "modp2048")
        ike_prop = f"{ike_enc}-{ike_auth}-{ike_pfs}"

        ipsec_enc = algo_map.get(conn.get("ipsec_enc", "aes-256"), "aes256")
        ipsec_auth = algo_map.get(conn.get("ipsec_auth", "sha256"), "sha256")
        ipsec_pfs = algo_map.get(conn.get("ipsec_pfs", "group14"), "modp2048")
        ipsec_prop = f"{ipsec_enc}-{ipsec_auth}-{ipsec_pfs}"

        local_ts_str = ", ".join(local_subnets) if local_subnets else "0.0.0.0/0"
        remote_ts_str = ", ".join(peer_cidrs) if peer_cidrs else "0.0.0.0/0"

        psk = conn.get("psk", "")
        display_psk = "********" if mask_psk else psk

        conns_block.append(f"""  {c_name} {{
    version = 2
    local_addrs = {outer_ip}
    remote_addrs = {peer_addr}
    proposals = {ike_prop}
    local {{
      auth = psk
      id = {outer_ip}
    }}
    remote {{
      auth = psk
      id = {peer_id}
    }}
    children {{
      {c_name} {{
        local_ts = {local_ts_str}
        remote_ts = {remote_ts_str}
        esp_proposals = {ipsec_prop}
        start_action = start
      }}
    }}
  }}""")

        secrets_block.append(f"""  ike-{c_name} {{
    id = {peer_id}
    secret = "{display_psk}"
  }}""")

    conns_str = "\n".join(conns_block)
    secrets_str = "\n".join(secrets_block)

    return f"""connections {{
{conns_str}
}}

secrets {{
{secrets_str}
}}
"""
