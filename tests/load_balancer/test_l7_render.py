from shared.haproxy_cfg import render


def test_l7_policy_renders_acl_and_backend():
    ls = {"id": "l1", "protocol": "HTTP", "protocol_port": 80, "default_pool_id": "p1",
          "policies": [{"action": "REDIRECT_TO_POOL", "redirect_pool_id": "p2", "position": 1,
                        "rules": [{"type": "PATH", "compare_type": "STARTS_WITH", "value": "/api"}]}],
          "pools": [{"id": p, "lb_algorithm": "LEAST_CONNECTIONS", "health_monitor": None,
                     "members": [{"id": "m" + p, "address": "10.0.0.9", "protocol_port": 80, "weight": 1}]} for p in ("p1", "p2")]}
    cfg = render({"vip_address": "10.0.0.5"}, [ls])
    assert "acl p0r0 path_beg /api" in cfg and "use_backend be_p2 if p0r0" in cfg
    assert "default_backend be_p1" in cfg and "balance leastconn" in cfg and "server m_mp2" in cfg