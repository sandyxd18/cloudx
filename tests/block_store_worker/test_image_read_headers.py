from services.block_store_worker.app import main as worker


class Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"id": "i1"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=0):
        return iter([b"data"])


def _download(monkeypatch):
    calls = []

    def get(url, **kw):
        calls.append((url, kw.get("headers")))
        return Resp()

    monkeypatch.setattr(worker.requests, "get", get)
    path, image = worker.download_image("img", "http://img-host:8002")
    import os
    os.unlink(path)
    assert image == {"id": "i1"}
    return calls


def test_block_store_download_image_sends_the_token_on_both_requests(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "s3cret")
    calls = _download(monkeypatch)
    assert calls == [
        ("http://img-host:8002/images/img", {"X-Internal-Token": "s3cret"}),
        ("http://img-host:8002/images/i1/download", {"X-Internal-Token": "s3cret"}),
    ]


def test_block_store_download_image_without_a_token_sends_no_token(monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)
    calls = _download(monkeypatch)
    assert len(calls) == 2
    assert all("X-Internal-Token" not in (headers or {}) for _, headers in calls)
