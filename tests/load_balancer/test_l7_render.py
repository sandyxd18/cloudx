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
def _r(rules, action="REDIRECT_TO_POOL", **kw):
    ls = {"id": "l1", "protocol": "HTTP", "protocol_port": 80, "default_pool_id": "p1",
          "policies": [{"action": action, "redirect_pool_id": "p2", "position": 1, "rules": rules, **kw}],
          "pools": [{"id": p, "lb_algorithm": "ROUND_ROBIN", "health_monitor": None,
                     "members": [{"id": "m" + p, "address": "10.0.0.9", "protocol_port": 80, "weight": 1}]} for p in ("p1", "p2")]}
    return render({"vip_address": "10.0.0.5"}, [ls])

def test_new_rule_types_render():
    R = lambda t, c, v, k=None, i=False: [{"type": t, "compare_type": c, "value": v, "key": k, "invert": i}]
    assert "acl p0r0 path_sub /adm" in _r(R("PATH", "CONTAINS", "/adm"))
    assert "acl p0r0 path_reg ^/v[0-9]+" in _r(R("PATH", "REGEX", "^/v[0-9]+"))
    assert "acl p0r0 hdr_sub(host) -i foo" in _r(R("HOST_NAME", "CONTAINS", "foo"))
    assert "acl p0r0 hdr(X-Env) prod" in _r(R("HEADER", "EQUAL_TO", "prod", "X-Env"))
    assert "acl p0r0 cook_beg(sid) ab" in _r(R("COOKIE", "STARTS_WITH", "ab", "sid"))
    assert "acl p0r0 path_end -i .jpg" in _r(R("FILE_TYPE", "EQUAL_TO", "jpg"))
    assert "use_backend be_p2 if !p0r0" in _r(R("PATH", "EQUAL_TO", "/a", None, True))

def test_redirects_render():
    r = [{"type": "PATH", "compare_type": "STARTS_WITH", "value": "/old", "key": None, "invert": False}]
    assert "http-request redirect location https://x.io/new code 301 if p0r0" in _r(r, "REDIRECT_TO_URL", redirect_url="https://x.io/new", redirect_http_code=301)
    assert "http-request redirect prefix https://x.io code 302\n" in _r([], "REDIRECT_PREFIX", redirect_prefix="https://x.io")

def test_header_rule_without_key_or_bad_key_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        _r([{"type": "HEADER", "compare_type": "EQUAL_TO", "value": "v", "key": None}])
    with pytest.raises(ValueError):
        _r([{"type": "HEADER", "compare_type": "EQUAL_TO", "value": "v", "key": "a b"}])