
def test_floating_ip_allocation_reuses_released_ip(networking):
    db = networking.db.SessionLocal()
    network = networking.models.Network(
        name="public-net",
        external=True,
        provider=True,
        provider_cidr="192.168.56.0/24",
        gateway_ip="192.168.56.1",
        allocation_start="192.168.56.100",
        allocation_end="192.168.56.101",
    )
    db.add(network)
    db.commit()
    db.refresh(network)

    first = networking.ipam.allocate_floating_ip(db, network)
    assert first == "192.168.56.100"
    db.add(networking.models.FloatingIP(name="fip-1", floating_ip=first, floating_ip_address=first, external_network_id=network.id))
    db.commit()
    assert networking.ipam.allocate_floating_ip(db, network) == "192.168.56.101"
    fip = networking.serializers.require_floating_ip(db, first)
    db.delete(fip)
    db.commit()
    assert networking.ipam.allocate_floating_ip(db, network) == "192.168.56.100"
    db.close()
