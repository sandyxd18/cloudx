import pytest
from shared.haproxy_cfg import render


def test_l7_policy_renders_acl_and_backend():
    ls = {"id": "l1", "protocol": "HTTP", "protocol_port": 80, "default_pool_id": "p1",
          "policies": [{"action": "REDIRECT_TO_POOL", "redirect_pool_id": "p2", "position": 1,
                        "rules": [{"type": "PATH", "compare_type": "STARTS_WITH", "value": "/api"}]}],
          "pools": [{"id": p, "lb_algorithm": "LEAST_CONNECTIONS", "health_monitor": None,
                     "members": [{"id": "m" + p, "address": "10.0.0.9", "protocol_port": 80, "weight": 1}]} for p in ("p1", "p2")]}
    cfg = render({"vip_address": "10.0.0.5"}, [ls])
    assert "acl p0r0 path_beg /api" in cfg and "http-request set-var(txn.pool) str(be_p2) if p0r0 !{ var(txn.pool) -m found }" in cfg
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
    assert "http-request set-var(txn.pool) str(be_p2) if !p0r0 !{ var(txn.pool) -m found }" in _r(R("PATH", "EQUAL_TO", "/a", None, True))

def test_redirects_render():
    r = [{"type": "PATH", "compare_type": "STARTS_WITH", "value": "/old", "key": None, "invert": False}]
    assert "http-request redirect location https://x.io/new code 301 if p0r0 !{ var(txn.pool) -m found }" in _r(r, "REDIRECT_TO_URL", redirect_url="https://x.io/new", redirect_http_code=301)
    assert "http-request redirect prefix https://x.io code 302 if !{ var(txn.pool) -m found }" in _r([], "REDIRECT_PREFIX", redirect_prefix="https://x.io")

def test_header_rule_without_key_or_bad_key_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        _r([{"type": "HEADER", "compare_type": "EQUAL_TO", "value": "v", "key": None}])
    with pytest.raises(ValueError):
        _r([{"type": "HEADER", "compare_type": "EQUAL_TO", "value": "v", "key": "a b"}])
def test_first_matching_policy_wins_across_actions():
    pools = [{"id": p, "lb_algorithm": "ROUND_ROBIN", "health_monitor": None,
              "members": [{"id": "m" + p, "address": "10.0.0.9", "protocol_port": 80, "weight": 1}]} for p in ("p1", "p2")]
    rule = [{"type": "PATH", "compare_type": "STARTS_WITH", "value": "/a", "key": None, "invert": False}]
    ls = {"id": "l1", "protocol": "HTTP", "protocol_port": 80, "default_pool_id": "p1", "pools": pools,
          "policies": [{"action": "REDIRECT_TO_POOL", "redirect_pool_id": "p2", "position": 1, "rules": rule},
                       {"action": "REJECT", "position": 2, "rules": rule}]}
    cfg = render({"vip_address": "10.0.0.5"}, [ls])
    lines = [l.strip() for l in cfg.splitlines()]
    pool_i = next(i for i, l in enumerate(lines) if l.startswith("http-request set-var(txn.pool)"))
    deny_i = next(i for i, l in enumerate(lines) if l.startswith("http-request deny"))
    assert pool_i < deny_i and "var(txn.pool) -m found" in lines[deny_i]
    assert lines.index("use_backend %[var(txn.pool)] if { var(txn.pool) -m found }") > deny_i

def test_ssl_rules_and_client_auth_render():
    R = lambda t, c, v, k=None: [{"type": t, "compare_type": c, "value": v, "key": k, "invert": False}]
    assert "acl p0r0 ssl_c_used\n" in _r(R("SSL_CONN_HAS_CERT", "EQUAL_TO", "TRUE"))
    cfg = _r(R("SSL_CONN_HAS_CERT", "EQUAL_TO", "false"))
    assert "acl p0r0 ssl_c_used\n" in cfg and "if !p0r0 " in cfg  # FALSE = the negated ACL
    assert "if p0r0 " in _r([{**R("SSL_CONN_HAS_CERT", "EQUAL_TO", "false")[0], "invert": True}])  # invert flips it back
    assert "acl p0r0 ssl_c_verify -m int 0\n" in _r(R("SSL_VERIFY_RESULT", "EQUAL_TO", "0"))
    assert "acl p0r0 ssl_c_s_dn(CN) -m str alice\n" in _r(R("SSL_DN_FIELD", "EQUAL_TO", "alice", "CN"))
    assert "acl p0r0 ssl_c_s_dn(O) -m sub corp\n" in _r(R("SSL_DN_FIELD", "CONTAINS", "corp", "O"))
    with pytest.raises(ValueError):
        _r(R("SSL_VERIFY_RESULT", "EQUAL_TO", "0 || true"))
    with pytest.raises(ValueError):
        _r(R("SSL_DN_FIELD", "EQUAL_TO", "a b", "CN"))
    ls = {"id": "l1", "protocol": "HTTPS", "protocol_port": 443, "ssl_cert_path": "/c.pem", "client_ca_path": "/ca.pem",
          "default_pool_id": None, "policies": [], "pools": []}
    for mode, want in (("MANDATORY", "ca-file /ca.pem verify required\n"), ("OPTIONAL", "ca-file /ca.pem verify optional crt-ignore-err all\n"), ("NONE", None)):
        cfg = render({"vip_address": "10.0.0.5"}, [{**ls, "client_authentication": mode}])
        assert (want in cfg) if want else ("ca-file" not in cfg)
