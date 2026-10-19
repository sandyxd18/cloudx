import pytest


def test_send_heartbeat_never_raises_and_posts_iqn(monkeypatch):
    pytest.importorskip("fastapi")
    from services.compute_worker.app import iscsi, main

    posts = []
    monkeypatch.setattr(iscsi, "initiator_iqn", lambda: "iqn.me")
    monkeypatch.setattr(main.requests, "post", lambda url, **kw: posts.append((url, kw["json"])))
    main.send_heartbeat()
    assert posts == [(f"{main.HA_API_URL}/internal/heartbeat/{main.COMPUTE_HOST}", {"initiator_iqn": "iqn.me"})]

    def boom(*a, **k):
        raise OSError("ha-api down")
    monkeypatch.setattr(main.requests, "post", boom)
    main.send_heartbeat()  # must not raise
