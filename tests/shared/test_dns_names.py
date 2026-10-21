import pytest
from shared import catalog, dns_names
from shared.dns_names import bucket_fqdn, fip_fqdn, internal_fqdn, lb_fqdn, slug
from shared.dns_validate import normalize_zone_name


def test_names_match_the_spec():
    assert fip_fqdn("203.0.113.10", "cloud.lab") == "203-0-113-10.compute.cloud.lab"
    assert lb_fqdn("My_LB", "abcdef12-0000", "cloud.lab") == "my-lb-abcdef.elb.cloud.lab"
    assert bucket_fqdn("Data", "11112222-3333", "cloud.lab") == "data-11112222.s3.cloud.lab"
    assert internal_fqdn("Web 1", "demo", "cloud.lab") == "web-1.demo.internal.cloud.lab"


def test_slug_is_always_a_valid_label():
    assert slug("My_LB!") == "my-lb"
    assert slug("---") == "x"
    assert slug("") == "x"
    assert len(slug("a" * 200)) <= 40
    for s in ("..", "a.b", "Ünï", " x "):
        normalize_zone_name(f"{slug(s)}.cloud.lab")  # must not raise


def _hosts(monkeypatch, **hosts):
    def url(service, interface="internal"):
        if service not in hosts:
            raise catalog.CatalogError(f"cannot resolve service '{service}'")
        return hosts[service]

    monkeypatch.setattr(catalog, "url", url)


def test_register_and_unregister_use_the_catalog_url(monkeypatch):
    _hosts(monkeypatch, dns="http://dns.test:8016/")
    seen = []

    def call(url, **kw):
        seen.append(url)
        return type("R", (), {"raise_for_status": lambda self: None})()

    monkeypatch.setattr(dns_names.requests, "post", call)
    monkeypatch.setattr(dns_names.requests, "delete", call)
    dns_names.register("floating_ip:1", "p", "1-1-1-1.compute.cloud.lab", "A", ["1.1.1.1"])
    dns_names.unregister("floating_ip:1")
    assert seen == ["http://dns.test:8016/internal/records"] * 2


def test_a_catalog_error_is_logged_not_raised(monkeypatch, caplog):
    _hosts(monkeypatch)
    monkeypatch.setattr(dns_names.requests, "post", lambda *a, **k: pytest.fail("no request without a URL"))
    monkeypatch.setattr(dns_names.requests, "delete", lambda *a, **k: pytest.fail("no request without a URL"))
    dns_names.register("floating_ip:1", "p", "1-1-1-1.compute.cloud.lab", "A", ["1.1.1.1"])
    dns_names.unregister("floating_ip:1")
    assert "cannot resolve service 'dns'" in caplog.text
