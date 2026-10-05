
def test_compute_worker_auto_recovery(monkeypatch):
    import services.compute_worker.app.main as worker_main

    # Mock conductor call to return a server in ACTIVE state whose domain is SHUTOFF
    fake_servers = [
        {
            "id": "srv-1",
            "name": "test-vm-crash",
            "status": "ACTIVE",
            "libvirt_domain_name": "dom-test-1",
        }
    ]

    restarted_domains = []

    monkeypatch.setattr(worker_main._rpc, "call", lambda target, method, args, timeout=10: {"servers": fake_servers})
    monkeypatch.setattr(worker_main.libvirt_driver, "get_all_domain_states", lambda: {"dom-test-1": "SHUTOFF"})
    # Mock libvirt_driver.start
    monkeypatch.setattr(worker_main.libvirt_driver, "start", lambda name: restarted_domains.append(name))
    monkeypatch.setattr(worker_main, "AUTO_RECOVERY", True)

    worker_main.sync_power_states()

    assert "dom-test-1" in restarted_domains
