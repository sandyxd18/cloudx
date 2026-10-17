from __future__ import annotations
import pytest
from shared.bird_cfg import render


def test_bird_cfg_render_basic():
    speaker = {
        "outer_ip": "172.24.4.50",
        "router_ext_ip": "172.24.4.1",
        "local_subnets": ["10.0.0.0/24"],
    }
    peers = [
        {
            "id": "bgppeer-12345",
            "name": "peer1",
            "peer_ip": "192.168.100.1",
            "remote_as": 65001,
            "local_as": 64512,
            "auth_password": "mysecretpassword",
            "advertised_prefixes": ["192.168.1.0/24"],
            "is_active": True,
        }
    ]

    cfg = render(speaker, peers, mask_password=False)
    assert "router id 172.24.4.50;" in cfg
    assert "protocol static static4 {" in cfg
    assert "route 192.168.1.0/24 blackhole;" in cfg
    assert "protocol bgp bgp_bgppeer_12345 {" in cfg
    assert "local 172.24.4.50 as 64512;" in cfg
    assert "neighbor 192.168.100.1 as 65001;" in cfg
    assert 'password "mysecretpassword";' in cfg
    assert "if net = 0.0.0.0/0 then reject;" in cfg
    assert "if net ~ [ 10.0.0.0/24+, 10.0.0.0/24- ] then reject;" in cfg
    assert 'if proto = "static4" then { bgp_next_hop = 172.24.4.1; accept; }' in cfg

    # Masked password test
    masked_cfg = render(speaker, peers, mask_password=True)
    assert 'password "***";' in masked_cfg


def test_bird_cfg_validation_errors():
    speaker = {
        "outer_ip": "invalid-ip",
        "router_ext_ip": "172.24.4.1",
        "local_subnets": [],
    }
    with pytest.raises(ValueError, match="Invalid outer_ip"):
        render(speaker, [])

    speaker["outer_ip"] = "172.24.4.50"
    speaker["router_ext_ip"] = "not-an-ip"
    with pytest.raises(ValueError, match="Invalid router_ext_ip"):
        render(speaker, [])

    speaker["router_ext_ip"] = "172.24.4.1"
    peers = [
        {
            "id": "p1",
            "peer_ip": "bad-ip",
            "remote_as": 65001,
            "local_as": 64512,
        }
    ]
    with pytest.raises(ValueError, match="Invalid peer_ip"):
        render(speaker, peers)

    # Invalid AS
    peers[0]["peer_ip"] = "192.168.1.1"
    peers[0]["remote_as"] = -10
    with pytest.raises(ValueError, match="Invalid remote_as"):
        render(speaker, peers)

    peers[0]["remote_as"] = 65001
    peers[0]["auth_password"] = 'bad"quote'
    with pytest.raises(ValueError, match="forbidden characters"):
        render(speaker, peers)

    peers[0]["auth_password"] = "newline\npass"
    with pytest.raises(ValueError, match="forbidden characters"):
        render(speaker, peers)


def test_bird_cfg_without_peers_and_subnets():
    speaker = {
        "outer_ip": "10.0.0.1",
        "router_ext_ip": "10.0.0.254",
        "local_subnets": [],
    }
    cfg = render(speaker, [], mask_password=False)
    assert "router id 10.0.0.1;" in cfg
    assert "protocol bgp" not in cfg


def test_bird_cfg_next_hop_and_supernet_overlap():
    cfg = render({"outer_ip": "172.17.0.50", "router_ext_ip": "172.17.0.99", "local_subnets": ["10.0.0.0/24"]},
                 [{"id": "p1", "name": "a", "peer_ip": "172.17.0.60", "remote_as": 65001, "local_as": 65000,
                   "auth_password": None, "advertised_prefixes": ["10.0.0.0/24"]}])
    assert "bgp_next_hop = 172.17.0.99" in cfg
    assert "password" not in cfg