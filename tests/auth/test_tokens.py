import re

import pytest
from fastapi import HTTPException

from tests.stack import fresh_service


@pytest.fixture
def auth(monkeypatch, tmp_path):
    return fresh_service("auth", monkeypatch, tmp_path)


def alice(auth, role="member"):
    return auth.models.User(id="u-1", username="alice", project_id="p-1", role=role)


def test_token_carries_identity_project_and_role(auth):
    claims = auth.tokens._decode_token(auth.tokens._create_token(alice(auth), project_name="demo"))
    assert (claims["sub"], claims["username"], claims["project_id"], claims["project_name"]) == ("u-1", "alice", "p-1", "demo")
    assert claims["role"] == "member" and claims["roles"] == ["member"] and claims["scope_type"] == "project"


def test_application_credential_roles_override_the_user_role(auth):
    claims = auth.tokens._decode_token(auth.tokens._create_token(alice(auth, "admin"), extra_roles=["reader"]))
    assert claims["role"] == "reader" and claims["roles"] == ["reader"]


def test_every_token_has_a_unique_id_for_revocation(auth):
    a, b = (auth.tokens._decode_token(auth.tokens._create_token(alice(auth)))["jti"] for _ in range(2))
    assert a != b


def test_tampered_token_is_rejected(auth):
    token = auth.tokens._create_token(alice(auth))
    head, payload, sig = token.split(".")
    with pytest.raises(HTTPException) as exc:
        auth.tokens._decode_token(f"{head}.{payload}.{sig[:-4]}AAAA")
    assert exc.value.status_code == 401


def test_expired_token_is_rejected(auth, monkeypatch):
    monkeypatch.setattr(auth.tokens, "TOKEN_EXPIRE_H", -1)
    with pytest.raises(HTTPException) as exc:
        auth.tokens._decode_token(auth.tokens._create_token(alice(auth)))
    assert exc.value.status_code == 401


def test_passwords_are_hashed_and_verified(auth):
    hashed = auth.tokens._hash_password("s3cret!")
    assert hashed != "s3cret!" and hashed.startswith("$2")
    assert auth.tokens._verify_password("s3cret!", hashed) is True
    assert auth.tokens._verify_password("wrong", hashed) is False


def test_generated_credentials_have_the_documented_shape(auth):
    ids = {auth.tokens._gen_access_key_id() for _ in range(50)}
    assert len(ids) == 50 and all(re.fullmatch(r"CLDX[A-Z0-9]{16}", i) for i in ids)
    assert len(auth.tokens._gen_secret_key()) >= 40
