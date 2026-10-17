from shared.swanctl_cfg import render

def test_render():
    gateway = {"outer_ip": "172.24.4.50"}
    connections = [
        {
            "id": "vpn-12345",
            "name": "conn1",
            "peer_address": "172.24.4.60",
            "peer_id": "172.24.4.60",
            "peer_cidrs": ["192.168.100.0/24"],
            "local_subnets": ["10.0.0.0/24"],
            "psk": "secret-key-123",
            "ike_enc": "aes-256",
            "ike_auth": "sha256",
            "ike_pfs": "group14",
            "ipsec_enc": "aes-256",
            "ipsec_auth": "sha256",
        }
    ]
    cfg = render(gateway, connections, mask_psk=False)
    assert "local_addrs = 172.24.4.50" in cfg
    assert "remote_addrs = 172.24.4.60" in cfg
    assert "aes256-sha256-modp2048" in cfg
    assert "local_ts = 10.0.0.0/24" in cfg
    assert "remote_ts = 192.168.100.0/24" in cfg
    assert 'secret = "secret-key-123"' in cfg

    masked_cfg = render(gateway, connections, mask_psk=True)
    assert 'secret = "********"' in masked_cfg
    print("test_swanctl_cfg passed successfully!")

if __name__ == "__main__":
    test_render()
