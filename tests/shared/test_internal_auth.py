"""Tests for UserOrInternal authentication dependency."""
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from shared import auth_middleware
from shared.auth_middleware import UserOrInternal

app = FastAPI()


@app.get("/resource")
def get_resource(current: UserOrInternal):
    return current


client = TestClient(app)


def _make_jwt(sub: str = "admin-id", role: str = "admin", username: str = "admin") -> str:
    payload = {
        "sub": sub,
        "username": username,
        "project_id": "admin-proj",
        "project_name": "admin",
        "role": role,
        "roles": [role],
        "scope_type": "system",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, auth_middleware.SECRET_KEY, algorithm=auth_middleware.ALGORITHM)


def test_user_or_internal_with_token_set(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "secret123")

    # Correct internal token
    res = client.get("/resource", headers={"X-Internal-Token": "secret123"})
    assert res.status_code == 200
    claims = res.json()
    assert claims["sub"] == "internal"
    assert claims["role"] == "admin"
    assert claims["username"] == "admin"
    assert claims["project_id"] == "admin"

    # Wrong internal token -> 401
    res = client.get("/resource", headers={"X-Internal-Token": "wrongtoken"})
    assert res.status_code == 401

    # Missing auth headers -> 401
    res = client.get("/resource")
    assert res.status_code == 401

    # Normal admin JWT works
    token = _make_jwt()
    res = client.get("/resource", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["sub"] == "admin-id"


def test_user_or_internal_with_token_unset(monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)

    # Header alone does not authenticate when INTERNAL_TOKEN is unset
    res = client.get("/resource", headers={"X-Internal-Token": "secret123"})
    assert res.status_code == 401
