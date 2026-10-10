def test_cors_origins_defaults_and_filtering(monkeypatch):
    from shared.cors import cors_origins

    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert cors_origins() == ["http://localhost"]

    monkeypatch.setenv("CORS_ORIGINS", "*")
    assert cors_origins() == ["http://localhost"]

    monkeypatch.setenv("CORS_ORIGINS", "http://app.local, *, https://example.com")
    assert cors_origins() == ["http://app.local", "https://example.com"]
