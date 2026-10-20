def _run(monkeypatch, tmp_path, flag):
    import services.compute_worker.app.main as w

    started, updated = [], []
    srv = [{"id": "s1", "name": "vm", "status": "ACTIVE", "libvirt_domain_name": "d1"}]
    monkeypatch.setattr(w, "reconcile_stale_domains", lambda: None)
    monkeypatch.setattr(w._rpc, "call", lambda *a, **k: {"servers": srv})
    monkeypatch.setattr(w.libvirt_driver, "get_all_domain_states", lambda: {"d1": "SHUTOFF"})
    monkeypatch.setattr(w.libvirt_driver, "start", started.append)
    monkeypatch.setattr(w, "conductor_update_server", lambda sid, body: updated.append(body))
    monkeypatch.setattr(w, "AUTO_RECOVERY", True)
    f = tmp_path / "fenced"
    if flag:
        f.write_text("[]")
    monkeypatch.setattr(w, "FENCED_FLAG", f)
    w.sync_power_states()
    return started, updated


def test_fenced_flag_blocks_restart_and_status_rewrite(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, True) == ([], [])


def test_without_flag_auto_recovery_restarts(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, False)[0] == ["d1"]
