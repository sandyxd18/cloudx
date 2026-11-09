"""The image a volume is created from is named by the user and goes into the image-api URL as one path segment: "."
and ".." are 404 and image-api is not asked; any other value is sent quoted as before."""
import pytest
import requests
from fastapi.testclient import TestClient
from shared.auth_middleware import require_auth
from tests.stack import fresh_service

IMAGE_URL = "http://image.test"
OWNER = {"project_id": "p1", "role": "member", "roles": ["member"], "sub": "u-owner", "scope_type": "project"}


class Reply:
    def __init__(self, status_code, body):
        self.status_code, self._body, self.text = status_code, body, ""

    def json(self):
        return self._body


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret")
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("IMAGE_API_URL", IMAGE_URL)
    monkeypatch.setenv("VOLUME_STORE_PATH", str(tmp_path / "volumes"))
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    svc = fresh_service("block_store", monkeypatch, tmp_path)
    svc.app.dependency_overrides[require_auth] = lambda: OWNER
    urls, casts = [], []

    def fake_get(url, headers=None, timeout=None, **kw):
        if url.startswith(IMAGE_URL):
            urls.append(url)
            return Reply(200, {"id": "img-id", "name": "x"})
        raise requests.ConnectionError("auth-api is not part of this test")  # the quota check skips it

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: casts.append(payload))
    monkeypatch.setattr(svc.clients, "_rpc", type("Rpc", (), {"publish_event": lambda *a, **k: None})())
    with svc.SessionLocal() as db:
        db.add(svc.VolumeType(id="vt", name="default", backend="lvm", is_public=True))
        db.commit()
    return type("Env", (), {"svc": svc, "client": TestClient(svc.app, raise_server_exceptions=False), "urls": urls,
                            "casts": casts})


def create(env, image):
    return env.client.post("/volumes", json={"name": "v1", "size_gb": 1, "image": image})


@pytest.mark.parametrize("ref", [".", ".."])
def test_a_dot_segment_as_the_image_is_404_and_image_api_is_not_asked(env, ref):
    r = create(env, ref)

    assert r.status_code == 404 and r.json() == {"detail": f"'{ref}' not found"}
    assert env.urls == [] and env.casts == []
    with env.svc.SessionLocal() as db:
        assert db.query(env.svc.Volume).count() == 0


@pytest.mark.parametrize("ref,segment", [("ubuntu", "ubuntu"), ("my image", "my%20image"), ("a/b", "a%2Fb")])
def test_the_image_is_sent_quoted_as_one_segment(env, ref, segment):
    r = create(env, ref)

    assert r.status_code == 201, r.text
    assert env.urls == [f"{IMAGE_URL}/images/{segment}"]
