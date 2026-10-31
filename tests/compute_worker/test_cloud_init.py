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


def test_network_config_for_an_ipv6_only_port():
    from services.compute_worker.app.cloudinit import render_network_config

    text = render_network_config({
        "ipv6_address": "fd42:7:7::2", "ipv6_cidr": "fd42:7:7::/64", "ipv6_gateway_ip": "fd42:7:7::1",
        "ipv6_dns_nameservers": ["2001:4860:4860::8888"], "mac_address": "02:75:C1:C6:AF:52",
    })
    assert "dhcp4: false" in text            # no DHCPv4 to wait for
    assert "addresses: [fd42:7:7::2/64]" in text
    assert "gateway6: fd42:7:7::1" in text and "gateway4" not in text
    assert "nameservers:\n      addresses: [2001:4860:4860::8888]" in text


def test_network_config_for_a_dual_stack_port():
    from services.compute_worker.app.cloudinit import render_network_config

    text = render_network_config({
        "fixed_ip": "10.10.0.2", "cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1", "dns_nameservers": ["1.1.1.1"],
        "ipv6_address": "fd42:7:7::2", "ipv6_cidr": "fd42:7:7::/64", "ipv6_gateway_ip": "fd42:7:7::1",
    })
    assert "addresses: [10.10.0.2/24, fd42:7:7::2/64]" in text
    assert "gateway4: 10.10.0.1" in text and "gateway6: fd42:7:7::1" in text
    assert "addresses: [1.1.1.1]" in text


def test_a_port_without_any_address_still_asks_dhcp():
    from services.compute_worker.app.cloudinit import render_network_config

    assert render_network_config({"mac_address": "02:00:00:00:00:01"}) == "version: 2\nethernets:\n  eth0:\n    dhcp4: true\n"


def test_the_default_user_data_keeps_the_images_own_user():
    # a `users:` list without `default` replaces the image's user: an Ubuntu image then had only `cirros`
    import pytest

    yaml = pytest.importorskip("yaml")
    from services.compute_worker.app.cloudinit import render_user_data

    config = yaml.safe_load(render_user_data("ssh-ed25519 AAAATEST user@example"))
    assert config["users"] == ["default"]
    assert config["ssh_authorized_keys"] == ["ssh-ed25519 AAAATEST user@example"]      # cloud-init gives them to that user


def test_the_default_user_data_without_a_key_has_no_users_list():
    import pytest

    yaml = pytest.importorskip("yaml")
    from services.compute_worker.app.cloudinit import render_user_data

    config = yaml.safe_load(render_user_data(None))
    assert "users" not in config and "ssh_authorized_keys" not in config
