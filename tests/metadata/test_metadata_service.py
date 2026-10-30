
def test_metadata_service_lookup_by_instance_id(monkeypatch):
    from services.metadata.app import metadata_service

    captured = {}

    def fake_compute_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {
            "uuid": "server-id",
            "hostname": "vm1",
            "name": "vm1",
            "public_keys": {"mykey": "ssh-rsa AAAATEST"},
            "user_data": "#cloud-config\n",
        }

    class FakeRequest:
        headers = {}

    monkeypatch.setattr(metadata_service, "compute_get", fake_compute_get)
    metadata = metadata_service.lookup_metadata(FakeRequest(), instance_id="server-id")
    assert metadata["uuid"] == "server-id"
    assert captured["path"] == "/servers/metadata-lookup"
    assert captured["params"] == {"instance_id": "server-id"}
    assert metadata_service.public_keys_index(metadata) == "0=mykey\n"
    assert metadata_service.first_public_key(metadata) == "ssh-rsa AAAATEST\n"
    assert metadata_service.default_user_data({}) == "#cloud-config\npackage_update: false\n"

def test_metadata_service_network_data_format():
    from services.metadata.app import metadata_service

    payload = metadata_service.network_data_json({
        "port_id": "port-id",
        "fixed_ip": "10.10.0.5",
        "mac_address": "02:00:00:00:00:05",
        "network_id": "network-id",
        "network": {"cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1", "dns_nameservers": ["1.1.1.1"]},
    })
    assert payload["links"][0]["id"] == "tapport-id"
    assert payload["networks"][0]["netmask"] == "255.255.255.0"
    assert payload["networks"][0]["routes"][0]["gateway"] == "10.10.0.1"
    assert payload["services"] == [{"type": "dns", "address": "1.1.1.1"}]


def test_network_data_for_an_ipv6_only_port_has_no_ipv4_entry():
    from services.metadata.app import metadata_service

    payload = metadata_service.network_data_json({
        "port_id": "port-id",
        "fixed_ip": "fd42:7:7::2",           # the server record shows the IPv6 address when there is no IPv4 one
        "mac_address": "02:00:00:00:00:05",
        "network": {"ipv6_address": "fd42:7:7::2", "ipv6_cidr": "fd42:7:7::/64", "ipv6_gateway_ip": "fd42:7:7::1"},
    })
    assert [n["type"] for n in payload["networks"]] == ["ipv6"]
    net = payload["networks"][0]
    assert (net["ip_address"], net["netmask"]) == ("fd42:7:7::2", "ffff:ffff:ffff:ffff::")
    assert net["routes"] == [{"network": "::", "netmask": "::", "gateway": "fd42:7:7::1"}]


def test_network_data_for_a_dual_stack_port_lists_both_on_one_link():
    from services.metadata.app import metadata_service

    payload = metadata_service.network_data_json({
        "port_id": "port-id",
        "fixed_ip": "10.10.0.5",
        "mac_address": "02:00:00:00:00:05",
        "network": {"cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1", "ipv6_address": "fd42:7:7::2", "ipv6_cidr": "fd42:7:7::/64"},
    })
    assert [(n["type"], n["ip_address"]) for n in payload["networks"]] == [("ipv4", "10.10.0.5"), ("ipv6", "fd42:7:7::2")]
    assert len(payload["links"]) == 1 and {n["link"] for n in payload["networks"]} == {payload["links"][0]["id"]}


def test_local_ipv4_is_never_an_ipv6_address(monkeypatch):
    from fastapi.testclient import TestClient

    from services.metadata.app import main

    monkeypatch.setattr(main, "lookup_metadata", lambda *a, **k: {"fixed_ip": "fd42:7:7::2", "network": {"ipv6_address": "fd42:7:7::2"}})
    client = TestClient(main.app)
    assert client.get("/latest/meta-data/local-ipv4").status_code == 404
    assert client.get("/2009-04-04/meta-data/local-ipv4").status_code == 404
