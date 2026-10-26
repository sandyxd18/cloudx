"""Default DNS of a subnet created without nameservers."""


def test_platform_resolver_when_the_dns_service_is_configured(networking, monkeypatch):
    monkeypatch.setenv("DNS_RESOLVER_ADDRESS", "10.9.0.5")
    monkeypatch.setenv("DNS_UPSTREAM", "1.1.1.1,8.8.8.8")
    assert networking.default_nameservers(4) == ["10.9.0.5"]


def test_upstream_resolvers_when_no_resolver_address_is_set(networking, monkeypatch):
    monkeypatch.delenv("DNS_RESOLVER_ADDRESS", raising=False)
    monkeypatch.setenv("DNS_UPSTREAM", "1.1.1.1, 8.8.8.8")
    assert networking.default_nameservers(4) == ["1.1.1.1", "8.8.8.8"]


def test_blank_resolver_address_counts_as_unset(networking, monkeypatch):
    monkeypatch.setenv("DNS_RESOLVER_ADDRESS", " ")
    monkeypatch.setenv("DNS_UPSTREAM", "9.9.9.9")
    assert networking.default_nameservers(4) == ["9.9.9.9"]


def test_no_default_when_nothing_is_configured(networking, monkeypatch):
    monkeypatch.delenv("DNS_RESOLVER_ADDRESS", raising=False)
    monkeypatch.delenv("DNS_UPSTREAM", raising=False)
    assert networking.default_nameservers(4) == []


def test_ipv6_gets_no_default(networking, monkeypatch):
    monkeypatch.setenv("DNS_RESOLVER_ADDRESS", "10.9.0.5")
    assert networking.default_nameservers(6) == []
