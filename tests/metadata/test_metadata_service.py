
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
