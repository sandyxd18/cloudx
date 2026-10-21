import pytest
import requests
from shared import catalog
from tests.stack import fresh_service


@pytest.fixture
def block(monkeypatch, tmp_path):
    monkeypatch.setenv("VOLUME_STORE_PATH", str(tmp_path / "volumes"))
    monkeypatch.setenv("STORAGE_LVM_DRIVER", "true")
    return fresh_service("block_store", monkeypatch, tmp_path)


def _capture_get(monkeypatch):
    seen = []

    class Resp:
        status_code = 200

        def json(self):
            return {"items": []}

    monkeypatch.setattr(requests, "get", lambda url, **kw: seen.append(url) or Resp())
    return seen


def test_compute_url_comes_from_the_catalog(block, monkeypatch):
    monkeypatch.delenv("COMPUTE_API_URL", raising=False)
    asked = []
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": asked.append(service) or "http://catalog-host:8003")
    seen = _capture_get(monkeypatch)
    db = block.SessionLocal()
    block.service._reconcile_orphan_volumes(db)
    db.close()
    assert asked == ["compute"]
    assert seen == ["http://catalog-host:8003/servers"]


def test_compute_url_override_still_wins(block, monkeypatch):
    monkeypatch.setenv("COMPUTE_API_URL", "http://override-host:1/")
    monkeypatch.setattr(catalog, "_catalog", lambda service: pytest.fail("the override must not consult the catalog"))
    seen = _capture_get(monkeypatch)
    db = block.SessionLocal()
    block.service._reconcile_orphan_volumes(db)
    db.close()
    assert seen == ["http://override-host:1/servers"]


def test_catalog_failure_does_not_break_the_orphan_reconcile(block, monkeypatch):
    monkeypatch.delenv("COMPUTE_API_URL", raising=False)

    def down(service, interface="internal"):
        raise catalog.CatalogError(f"cannot resolve service '{service}'")

    monkeypatch.setattr(catalog, "url", down)
    db = block.SessionLocal()
    block.service._reconcile_orphan_volumes(db)  # logs a warning, like an unreachable compute-api
    db.close()


@pytest.fixture
def casts(block, monkeypatch):
    sent = []

    class FakeRpc:
        def cast(self, routing_key, method, payload):
            sent.append(payload)

        def publish_event(self, *a, **k):
            pass

    monkeypatch.setattr(block.clients, "_rpc", FakeRpc())
    db = block.SessionLocal()
    db.add(block.VolumeType(id="vt", name="default", backend="lvm", is_public=True))
    db.commit()
    db.close()
    return sent


USER = {"project_id": "proj-1", "role": "admin"}


def test_image_volume_hands_the_worker_the_catalog_image_url(block, casts, monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": f"http://catalog-{service}:8002/")
    db = block.SessionLocal()
    block.create_volume(block.VolumeIn(name="v1", size_gb=1, image="img1"), USER, db)
    db.close()
    assert casts[-1]["_image_api_url"] == "http://catalog-image:8002"


def test_blank_volume_does_not_need_the_image_service(block, casts, monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda *a, **k: pytest.fail("a blank volume must not resolve the image service"))
    db = block.SessionLocal()
    block.create_volume(block.VolumeIn(name="v2", size_gb=1), USER, db)
    db.close()
    assert casts[-1]["_image_api_url"] is None


def test_image_volume_with_unresolvable_image_service_is_502_and_leaves_no_record(block, casts, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.delenv("IMAGE_API_URL", raising=False)

    def down(service, interface="internal"):
        raise catalog.CatalogError(f"cannot resolve service '{service}'")

    monkeypatch.setattr(catalog, "url", down)
    db = block.SessionLocal()
    with pytest.raises(HTTPException) as exc:
        block.create_volume(block.VolumeIn(name="v3", size_gb=1, image="img1"), USER, db)
    assert exc.value.status_code == 502 and "image" in exc.value.detail
    assert block.get_volume(db, "v3") is None
    db.close()
