import json


def test_cloud_init_rendering():
    from services.compute_worker.app.cloudinit import (
        render_meta_data,
        render_network_config,
        render_user_data,
    )

    assert json.loads(render_meta_data("server-id", "vm1")) == {"instance-id": "server-id", "local-hostname": "vm1"}
    with_key = json.loads(render_meta_data("server-id", "vm1", public_key="ssh-rsa AAAA user@x", key_name="k1"))
    assert with_key["public-keys"] == {"k1": "ssh-rsa AAAA user@x", "0": "ssh-rsa AAAA user@x"}
    user_data = render_user_data("ssh-rsa AAAATEST user@example")
    assert user_data.startswith("#cloud-config")
    assert "package_update: false" in user_data
    assert "resize_rootfs: true" in user_data
    assert "ssh_authorized_keys:" in user_data
    assert "ssh-rsa AAAATEST user@example" in user_data
    network_config = render_network_config({"fixed_ip": "10.10.0.2", "cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1", "dns_nameservers": ["1.1.1.1"]})
    assert "addresses: [10.10.0.2/24]" in network_config
    assert "gateway4: 10.10.0.1" in network_config
    by_mac = render_network_config({"fixed_ip": "10.10.0.2", "cidr": "10.10.0.0/24", "mac_address": "02:75:C1:C6:AF:52"})
    assert 'match: {macaddress: "02:75:c1:c6:af:52"}' in by_mac and "set-name: eth0" in by_mac
    assert "match:" not in network_config
