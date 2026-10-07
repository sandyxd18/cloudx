import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _req(auth=None):
    return Request({"type": "http", "headers": [(b"authorization", auth.encode())] if auth else []})


def test_metrics_token(monkeypatch):
    from shared.metrics import check_metrics_token as chk
    monkeypatch.setenv("METRICS_TOKEN", "s3")
    chk(_req("Bearer s3"))
    for bad in (None, "Bearer x"):
        with pytest.raises(HTTPException):
            chk(_req(bad))
    monkeypatch.delenv("METRICS_TOKEN")
    chk(_req())  # unset = open (upgrade path)
