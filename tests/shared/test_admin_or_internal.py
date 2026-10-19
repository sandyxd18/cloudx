import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from shared.auth_middleware import require_admin_or_internal  # noqa: E402


def test_internal_token_ok_and_anonymous_rejected(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")
    app = FastAPI()
    app.get("/x")(lambda who=Depends(require_admin_or_internal): who["role"])
    c = TestClient(app)
    assert c.get("/x", headers={"X-Internal-Token": "tok"}).json() == "admin"
    assert c.get("/x").status_code == 401
    assert c.get("/x", headers={"X-Internal-Token": "wrong"}).status_code == 401