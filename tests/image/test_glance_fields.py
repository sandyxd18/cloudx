import hashlib
import importlib
import io

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def img(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'i.sqlite'}")
    monkeypatch.setenv("IMAGE_STORE_PATH", str(tmp_path / "store"))
    import services.image.app.main as m

    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    from shared.auth_middleware import require_auth, require_user_or_internal

    for dep in (require_auth, require_user_or_internal):
        m.app.dependency_overrides[dep] = lambda: {"project_id": "p1", "role": "admin"}
    m.client = TestClient(m.app)
    return m


def upload(m, name="i1", data=b"hello"):
    return m.client.post("/images", data={"name": name, "disk_format": "raw"}, files={"file": ("f.img", io.BytesIO(data))})


def test_upload_goes_through_saving_to_active_with_hashes(img):
    seen = []
    orig = img.FileStorageBackend.save_image
    img.FileStorageBackend.save_image = lambda self, f, n: (seen.append(img.client.get("/images/i1").status_code), orig(self, f, n))[1]
    body = upload(img).json()
    assert body["status"] == "ACTIVE" and body["os_hash_algo"] == "sha512"
    assert body["os_hash_value"] == hashlib.sha512(b"hello").hexdigest() and body["properties"] == {}
    assert seen == [200]  # record already existed (as 'saving') while bytes were being written


def test_properties_merge_and_protected_blocks_delete(img):
    upload(img)
    r = img.client.patch("/images/i1", json={"properties": {"os_distro": "ubuntu", "x": "1"}, "protected": True, "min_ram_mb": 512})
    assert r.json()["properties"] == {"os_distro": "ubuntu", "x": "1"} and r.json()["min_ram_mb"] == 512
    assert img.client.patch("/images/i1", json={"properties": {"x": None}}).json()["properties"] == {"os_distro": "ubuntu"}
    assert img.client.delete("/images/i1").status_code == 403
    img.client.patch("/images/i1", json={"protected": False})
    assert img.client.delete("/images/i1").status_code == 200


def test_inspect_rejects_wrong_format(img, tmp_path, monkeypatch):
    class R:
        returncode, stdout, stderr = 0, '{"format": "raw", "virtual-size": 5}', ""

    monkeypatch.setattr(img.proc, "run", lambda *a, **k: R())
    assert img._inspect(tmp_path, "raw") == 5
    with pytest.raises(Exception) as e:
        img._inspect(tmp_path, "qcow2")
    assert e.value.status_code == 400
