import pytest


def test_send_heartbeat_never_raises_and_posts_iqn(monkeypatch):
    pytest.importorskip("fastapi")
    from services.compute_worker.app import iscsi, main

    posts = []
    monkeypatch.setattr(iscsi, "initiator_iqn", lambda: "iqn.me")
    monkeypatch.setattr(main.requests, "post", lambda url, **kw: posts.append((url, kw["json"])))
    main.send_heartbeat()
    assert posts == [(f"{main.HA_API_URL}/internal/heartbeat/{main.COMPUTE_HOST}", {"initiator_iqn": "iqn.me"})]

    main.HA_API_URL = "http://a:1, http://b:1/"
    posts.clear()
    def first_down(url, **kw):
        if "//a:" in url:
            raise OSError("down")
        posts.append(url)
    monkeypatch.setattr(main.requests, "post", first_down)
    main.send_heartbeat()
    assert posts == [f"http://b:1/internal/heartbeat/{main.COMPUTE_HOST}"]   # first answering instance only

    def boom(*a, **k):
        raise OSError("ha-api down")
    monkeypatch.setattr(main.requests, "post", boom)
    main.send_heartbeat()  # must not raise
