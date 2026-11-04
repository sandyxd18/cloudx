import pytest
from services.compute_worker.app import image_manager, worker
from shared import catalog


class Resp:
    status_code = 200

    def __init__(self, body=None):
        self._body = body or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=0):
        return iter([b"data"])


@pytest.fixture(autouse=True)
def _image_host(monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    monkeypatch.setattr(catalog, "url", lambda service, interface="internal": "http://img-host:8002")


def _spy(monkeypatch, module):
    calls = []

    def get(url, **kw):
        calls.append((url, kw.get("headers")))
        return Resp({"id": "i1"})

    monkeypatch.setattr(module.requests, "get", get)
    return calls


def test_fetch_image_sends_the_internal_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret")
    calls = _spy(monkeypatch, worker)
    worker.fetch_image("i1")
    assert calls == [("http://img-host:8002/images/i1", {"X-Internal-Token": "s3cret"})]


def test_fetch_image_without_a_token_sends_no_token(monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)
    calls = _spy(monkeypatch, worker)
    assert worker.fetch_image("i1") == {"id": "i1"}
    assert "X-Internal-Token" not in (calls[0][1] or {})


def test_fetch_image_meta_and_download_image_send_the_internal_token(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret")
    monkeypatch.setattr(image_manager, "IMAGE_CACHE_DIR", tmp_path)
    calls = _spy(monkeypatch, image_manager)
    image_manager.fetch_image_meta("i1")
    image_manager.download_image({"id": "i1", "filename": "a.qcow2"})
    assert calls == [
        ("http://img-host:8002/images/i1", {"X-Internal-Token": "s3cret"}),
        ("http://img-host:8002/images/i1/download", {"X-Internal-Token": "s3cret"}),
    ]


def test_image_manager_without_a_token_sends_no_token(monkeypatch, tmp_path):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr(image_manager, "IMAGE_CACHE_DIR", tmp_path)
    calls = _spy(monkeypatch, image_manager)
    image_manager.fetch_image_meta("i1")
    image_manager.download_image({"id": "i1", "filename": "a.qcow2"})
    assert len(calls) == 2
    assert all("X-Internal-Token" not in (headers or {}) for _, headers in calls)
