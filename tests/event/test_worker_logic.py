def test_backoff_seconds(monkeypatch):
    import services.event_worker.app.main as w

    monkeypatch.setattr(w, "BASE_RETRY_DELAY", 60)
    assert [w.backoff_seconds(n) for n in (1, 2, 3, 4)] == [60, 120, 240, 480]
    assert w.backoff_seconds(0) == 60
    assert w.backoff_seconds(50) == 3600
