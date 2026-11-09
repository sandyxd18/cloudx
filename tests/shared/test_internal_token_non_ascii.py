"""A non-ASCII X-Internal-Token header is a wrong token (401 / anonymous), never a 500."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from shared.auth_middleware import (  # noqa: E402
    OptionalUserOrInternal,
    require_admin_or_internal,
    require_internal,
    require_user_or_internal,
)

CAFE = "café".encode("latin-1")  # the bytes of a non-ASCII header; the server reads them as the str "café"


def make_app():
    app = FastAPI()
    @app.get("/optional")
    def optional(current: OptionalUserOrInternal):
        return {"who": current}

    app.get("/user-or-internal")(lambda who=Depends(require_user_or_internal): who["sub"])
    app.get("/admin-or-internal")(lambda who=Depends(require_admin_or_internal): who["sub"])
    app.get("/internal")(lambda _=Depends(require_internal): "ok")
    return app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    return TestClient(make_app())


def test_the_optional_dependency_treats_it_as_anonymous(client):
    r = client.get("/optional", headers={"X-Internal-Token": CAFE})
    assert r.status_code == 200
    assert r.json() == {"who": None}


@pytest.mark.parametrize("path", ["/user-or-internal", "/admin-or-internal", "/internal"])
def test_the_strict_dependencies_answer_401(client, path):
    assert client.get(path, headers={"X-Internal-Token": CAFE}).status_code == 401


@pytest.mark.parametrize("path", ["/optional", "/user-or-internal", "/admin-or-internal", "/internal"])
def test_a_correct_token_still_works(client, path):
    assert client.get(path, headers={"X-Internal-Token": "tok"}).status_code == 200


def test_metrics_token_non_ascii_is_a_401(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    from shared.metrics import check_metrics_token

    monkeypatch.setenv("METRICS_TOKEN", "s3")

    def req(value):
        return Request({"type": "http", "headers": [(b"authorization", value)]})

    with pytest.raises(HTTPException) as exc:
        check_metrics_token(req(b"Bearer \xe9"))
    assert exc.value.status_code == 401
    check_metrics_token(req(b"Bearer s3"))
