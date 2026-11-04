"""X-Caller-Claims: a service holding INTERNAL_TOKEN states its caller's identity; OptionalUserOrInternal honours it."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

from shared import auth_middleware as am  # noqa: E402
from shared.auth_middleware import OptionalUserOrInternal  # noqa: E402
from shared.internal import on_behalf_headers  # noqa: E402

HEADER = "X-Caller-Claims"
MEMBER = {"sub": "u1", "username": "alice", "project_id": "p1", "role": "member", "roles": ["member"],
          "scope_type": "project", "jti": "j", "exp": 123, "project_name": "alpha"}
DOMAIN_ADMIN = {"sub": "u2", "username": "bob", "project_id": "p2", "role": "member", "roles": ["member"],
                "domain_admin": True, "domain_id": "d1", "scope_type": "project"}
INTERNAL = {"sub": "internal", "username": "admin", "project_id": "admin", "role": "admin", "roles": ["admin"],
            "scope_type": "system", "jti": "internal"}
KEPT_KEYS = {"sub", "username", "project_id", "role", "roles", "domain_admin", "domain_id", "scope_type"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("INTERNAL_TOKEN", "tok")


def make_app():
    app = FastAPI()

    @app.get("/who")
    def who(current: OptionalUserOrInternal):
        return {"who": current}

    return app


def who(headers):
    r = TestClient(make_app()).get("/who", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["who"]


def user_jwt():
    claims = {"sub": "jwt-user", "username": "j", "project_id": "pj", "role": "member", "roles": ["member"],
              "scope_type": "project", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    return jwt.encode(claims, am.SECRET_KEY, algorithm=am.ALGORITHM)


def claims_headers(value, token="tok"):
    return {"X-Internal-Token": token, HEADER: value if isinstance(value, str) else json.dumps(value)}


VALID = {"sub": "u1", "username": "alice", "project_id": "p1", "role": "member", "roles": ["member"],
         "domain_admin": False, "domain_id": "d1", "scope_type": "project"}


# ── sender ───────────────────────────────────────────────────────────────────

def test_sender_states_a_member_and_drops_other_keys():
    h = on_behalf_headers(MEMBER)
    assert h["X-Internal-Token"] == "tok"
    claims = json.loads(h[HEADER])
    assert claims == {"sub": "u1", "username": "alice", "project_id": "p1", "role": "member", "roles": ["member"],
                      "scope_type": "project"}
    assert set(claims) <= KEPT_KEYS
    assert " " not in h[HEADER]  # compact separators


def test_sender_states_a_domain_admin():
    claims = json.loads(on_behalf_headers(DOMAIN_ADMIN)[HEADER])
    assert claims["domain_admin"] is True and claims["domain_id"] == "d1"


@pytest.mark.parametrize("current", [None, INTERNAL, {"sub": "internal"}], ids=["none", "internal", "bare-internal"])
def test_sender_gives_the_internal_token_alone_for_none_and_the_internal_caller(current):
    assert on_behalf_headers(current) == {"X-Internal-Token": "tok"}


def test_sender_gives_nothing_without_an_internal_token(monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN")
    for current in (MEMBER, None, INTERNAL):
        assert on_behalf_headers(current) == {}


def test_sender_keeps_the_header_ascii_for_a_non_ascii_username():
    h = on_behalf_headers({**MEMBER, "username": "josé"})
    h[HEADER].encode("ascii")
    assert json.loads(h[HEADER])["username"] == "josé"
    # and the receiver reads it back
    assert who(h)["username"] == "josé"


def test_sender_skips_none_values():
    claims = json.loads(on_behalf_headers({**MEMBER, "domain_id": None, "project_id": None})[HEADER])
    assert "domain_id" not in claims and "project_id" not in claims


def test_sender_raises_when_the_claims_exceed_2048_bytes():
    with pytest.raises(ValueError):
        on_behalf_headers({**MEMBER, "roles": [f"role-{i:04d}" for i in range(400)]})


@pytest.mark.parametrize("bad", [{}, {"role": None}, {"role": ""}, {"role": 1}, {"role": ["admin"]}, {"sub": "u1"}],
                         ids=["absent", "null", "empty", "int", "list", "only-sub"])
def test_sender_raises_when_the_role_is_not_a_non_empty_string(bad):
    with pytest.raises(ValueError):
        on_behalf_headers({"project_id": "p1", **bad})


def test_sender_raises_valueerror_for_claims_json_cannot_serialise():
    with pytest.raises(ValueError):
        on_behalf_headers({**MEMBER, "username": object()})
    with pytest.raises(ValueError):
        on_behalf_headers({**MEMBER, "roles": [{1, 2}]})


def test_sender_accepts_claims_just_under_the_limit():
    base = len(json.dumps({"role": "member", "roles": [""]}, separators=(",", ":")))
    pad = "x" * (2048 - base)
    h = on_behalf_headers({"role": "member", "roles": [pad]})
    assert len(h[HEADER].encode()) == 2048
    assert who(h)["roles"] == [pad]


# ── receiver: a valid internal token ─────────────────────────────────────────

def test_valid_token_and_valid_claims_give_exactly_those_claims():
    assert who(claims_headers({**VALID, "jti": "x", "exp": 1, "project_name": "p"})) == VALID


def test_round_trip_of_the_sender():
    assert who(on_behalf_headers(DOMAIN_ADMIN)) == {k: v for k, v in DOMAIN_ADMIN.items() if k in KEPT_KEYS}


def test_optional_string_claims_may_be_absent_or_null():
    assert who(claims_headers({"role": "member"})) == {"role": "member"}
    assert who(claims_headers({"role": "member", "project_id": None, "domain_id": None})) == {"role": "member"}


def test_a_null_optional_key_reads_like_an_absent_one():
    nulls = {"role": "member", "sub": None, "username": None, "project_id": None, "domain_id": None, "scope_type": None}
    assert who(claims_headers(nulls)) == who(claims_headers({"role": "member"})) == {"role": "member"}
    assert "domain_id" not in who(claims_headers({"role": "member", "domain_id": None}))


def test_valid_token_and_no_header_give_the_synthetic_admin():
    got = who({"X-Internal-Token": "tok"})
    assert got["sub"] == "internal" and got["role"] == "admin"


def test_valid_token_ignores_a_jwt_when_claims_are_given():
    got = who({**claims_headers(VALID), "X-Auth-Token": user_jwt()})
    assert got == VALID


BIG_ROLES = {"role": "member", "roles": [f"r{i:04d}" for i in range(400)]}
INVALID = {
    "not json": "{nope",
    "empty": "",
    "list": "[]",
    "string": '"member"',
    "number": "1",
    "null": "null",
    "oversized": json.dumps(BIG_ROLES, separators=(",", ":")),
    "role missing": json.dumps({"project_id": "p1"}),
    "role not a string": json.dumps({"role": 1}),
    "role null": json.dumps({"role": None}),
    "role empty": json.dumps({"role": ""}),
    "role a list": json.dumps({"role": ["admin"]}),
    "project_id not a string": json.dumps({"role": "member", "project_id": 5}),
    "domain_id not a string": json.dumps({"role": "member", "domain_id": ["d"]}),
    "sub not a string": json.dumps({"role": "member", "sub": 1}),
    "username not a string": json.dumps({"role": "member", "username": {}}),
    "scope_type not a string": json.dumps({"role": "member", "scope_type": True}),
    "domain_admin string": json.dumps({"role": "member", "domain_admin": "true"}),
    "domain_admin int": json.dumps({"role": "member", "domain_admin": 1}),
    "domain_admin null": json.dumps({"role": "member", "domain_admin": None}),
    "roles string": json.dumps({"role": "member", "roles": "admin"}),
    "roles of non-str": json.dumps({"role": "member", "roles": ["member", 3]}),
    "roles null": json.dumps({"role": "member", "roles": None}),
    "roles object": json.dumps({"role": "member", "roles": {"admin": True}}),
    "deeply nested": "[" * 1024 + "]" * 1024,  # exactly 2048 bytes: within the limit, so it reaches the parser
}


@pytest.mark.parametrize("value", INVALID.values(), ids=INVALID.keys())
def test_valid_token_and_an_invalid_header_give_anonymous_never_admin(value):
    assert who(claims_headers(value)) is None


def raw_who(*claims_values, token=b"tok"):
    """optional_user_or_internal on a hand-built ASGI request: the test client re-encodes header bytes as UTF-8."""
    from starlette.requests import Request

    headers = [(b"x-internal-token", token)] + [(b"x-caller-claims", v) for v in claims_values]
    return am.optional_user_or_internal(Request({"type": "http", "method": "GET", "path": "/", "headers": headers}))


def test_bad_utf8_in_the_header_is_anonymous():
    for raw in (b'{"role":"member","username":"\xff\xfe"}', b'{"role":"mem\xc3"}'):
        assert raw_who(raw) is None, raw


def test_valid_utf8_non_ascii_raw_bytes_are_read():
    assert raw_who('{"role":"member","username":"josé"}'.encode()) == {"role": "member", "username": "josé"}


def test_the_limit_is_measured_on_the_raw_value_in_bytes():
    # 2048 bytes passes, 2049 does not; non-ASCII counts as its UTF-8 bytes
    def raw(n_bytes):
        base = len(b'{"role":"member","username":""}')
        return b'{"role":"member","username":"' + b"x" * (n_bytes - base) + b'"}'

    assert raw_who(raw(2048)) is not None
    assert raw_who(raw(2049)) is None
    two_byte = b'{"role":"member","username":"' + "é".encode() * 1010 + b'"}'  # ~1030 characters, > 2048 bytes
    assert len(two_byte) > 2048
    assert raw_who(two_byte) is None


def test_two_claims_headers_are_invalid():
    assert raw_who(b'{"role":"member"}', b'{"role":"admin"}') is None


def test_claims_that_say_admin_are_admin_but_nothing_more_is_added():
    got = who(claims_headers({"role": "admin"}))
    assert got == {"role": "admin"}  # no synthetic sub/project/roles filled in


def test_claims_that_say_member_never_become_admin():
    got = who(claims_headers({"role": "member", "roles": ["member"]}))
    assert got["role"] == "member" and got.get("sub") != "internal"


# ── receiver: no valid internal token, the header is ignored ─────────────────

def test_without_a_valid_internal_token_the_header_is_ignored(monkeypatch):
    for headers in (
        {HEADER: json.dumps(VALID)},
        {"X-Internal-Token": "wrong", HEADER: json.dumps(VALID)},
        {"X-Internal-Token": "", HEADER: json.dumps(VALID)},
        {"X-Internal-Token": "tok ", HEADER: json.dumps(VALID)},
    ):
        assert who(headers) is None, headers


def test_without_a_valid_internal_token_the_jwt_decides():
    for token_header in ({}, {"X-Internal-Token": "wrong"}, {"X-Internal-Token": ""}):
        got = who({**token_header, HEADER: json.dumps(VALID), "X-Auth-Token": user_jwt()})
        assert got["sub"] == "jwt-user" and got["project_id"] == "pj"


def test_an_invalid_header_without_a_valid_token_is_ignored_not_anonymous_by_force():
    got = who({"X-Internal-Token": "wrong", HEADER: "{nope", "X-Auth-Token": user_jwt()})
    assert got["sub"] == "jwt-user"


def test_with_internal_token_unset_the_header_and_any_token_are_ignored(monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN")
    for headers in ({HEADER: json.dumps(VALID)},
                    {"X-Internal-Token": "", HEADER: json.dumps(VALID)},
                    {"X-Internal-Token": "tok", HEADER: json.dumps(VALID)}):
        assert who(headers) is None
    assert who({"X-Internal-Token": "tok", HEADER: json.dumps(VALID), "X-Auth-Token": user_jwt()})["sub"] == "jwt-user"


def test_with_internal_token_set_to_empty_the_header_is_ignored(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "")
    assert who({"X-Internal-Token": "", HEADER: json.dumps(VALID)}) is None


def test_other_dependencies_do_not_read_the_header():
    from shared.auth_middleware import UserOrInternal

    app = FastAPI()

    @app.get("/strict")
    def strict(current: UserOrInternal):
        return {"who": current}

    r = TestClient(app).get("/strict", headers=claims_headers({"role": "member", "project_id": "p1"}))
    assert r.json()["who"]["sub"] == "internal"  # the synthetic admin, as before: claims are not read here
