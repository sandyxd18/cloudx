import pytest

from shared.dns_render import hostmaster_from_email, render_named_conf, render_zone

CFG = {"root": "/var/lib/cloudx/dns/auth", "address": "172.20.0.120", "control_port": 953,
       "forwarders": ["1.1.1.1", "8.8.8.8"], "clients": ["10.0.0.0/8"]}
RCFG = {**CFG, "root": "/var/lib/cloudx/dns/resolver", "address": "172.20.0.118", "control_port": 954}


def zone(name, scope="public", secondaries=()):
    return {"name": name, "scope": scope, "serial": 7, "ttl": 300, "secondaries": list(secondaries)}


def test_render_zone():
    out = render_zone(zone("example.com"), [
        {"name": "@", "type": "A", "ttl": None, "records": ["1.2.3.4"]},
        {"name": "www", "type": "CNAME", "ttl": 60, "records": ["example.com."]},
        {"name": "@", "type": "TXT", "ttl": None, "records": ['v=spf1 "x" ; \\y']},
    ], "ns1.cloud.lab.", "hostmaster.cloud.lab.")
    assert "$ORIGIN example.com." in out and "$TTL 300" in out
    assert "@ IN SOA ns1.cloud.lab. hostmaster.cloud.lab. 7 " in out
    assert "@ IN NS ns1.cloud.lab." in out
    assert "@ 300 IN A 1.2.3.4" in out or "@ IN A 1.2.3.4" in out
    assert "www 60 IN CNAME example.com." in out
    assert '@ IN TXT "v=spf1 \\"x\\" ; \\\\y"' in out or '@ 300 IN TXT "v=spf1 \\"x\\" ; \\\\y"' in out


def test_txt_is_split_in_255_byte_strings_and_never_breaks_a_line():
    out = render_zone(zone("example.com"), [{"name": "@", "type": "TXT", "ttl": None, "records": ["a" * 600]}],
                      "ns1.cloud.lab.", "h.cloud.lab.")
    line = next(x for x in out.splitlines() if " TXT " in x)
    assert line.count('"') == 6 and "\n" not in line


def test_hostmaster():
    assert hostmaster_from_email("admin@example.com") == "admin.example.com."
    assert hostmaster_from_email("a.b@example.com") == "a\\.b.example.com."


def test_auth_conf_never_contains_internal_zones():
    zones = [zone("example.com", secondaries=["203.0.113.5"]), zone("internal.cloud.lab", "internal"), zone("plain.org")]
    out = render_named_conf("auth", zones, CFG)
    assert 'zone "example.com"' in out and 'zone "plain.org"' in out
    assert "internal.cloud.lab" not in out
    assert "recursion no" in out and "listen-on port 53 { 172.20.0.120; }" in out
    assert "allow-transfer { 203.0.113.5; }" in out and "also-notify { 203.0.113.5; }" in out
    plain = out.split('zone "plain.org"')[1]
    assert "allow-transfer { none; }" in plain and "notify no" in plain
    assert 'include "/var/lib/cloudx/dns/auth/rndc.key"' in out and "port 953" in out


def test_resolver_conf():
    zones = [zone("example.com"), zone("internal.cloud.lab", "internal")]
    out = render_named_conf("resolver", zones, RCFG)
    assert 'zone "internal.cloud.lab"' in out and 'zone "example.com"' in out
    assert "recursion yes" in out and "listen-on port 53 { 172.20.0.118; }" in out
    assert "forwarders { 1.1.1.1; 8.8.8.8; }" in out
    assert "allow-recursion { clients; }" in out and "10.0.0.0/8" in out and "127.0.0.0/8" in out
    assert "dnssec-validation no" in out
    assert "also-notify" not in out and "allow-transfer { none; }" in out


@pytest.mark.parametrize("bad", ['x" { }; zone "y', "a b.com", "a.com\n", "../etc.com"])
def test_conf_refuses_unsafe_zone_names(bad):
    with pytest.raises(ValueError):
        render_named_conf("auth", [zone(bad)], CFG)


def test_conf_refuses_unsafe_addresses():
    with pytest.raises(ValueError):
        render_named_conf("auth", [zone("example.com", secondaries=["1.1.1.1; };"])], CFG)
    with pytest.raises(ValueError):
        render_named_conf("resolver", [], {**RCFG, "clients": ["any; };"]})
