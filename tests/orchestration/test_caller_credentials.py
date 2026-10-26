import importlib

import pytest
from fastapi import HTTPException


@pytest.fixture
def orch(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'o.sqlite'}")
    import services.orchestration.app.main as m

    m = importlib.reload(m)
    importlib.reload(m.stacks)
    return m


def eng():
    return importlib.import_module("services.orchestration.app.engine")


class Req:
    def __init__(self, **headers):
        self.headers = headers


def test_authorization_header_is_forwarded_as_is(orch):
    assert eng().caller_authorization(Req(Authorization="Bearer abc").headers) == "Bearer abc"


def test_x_auth_token_is_turned_into_a_bearer_header(orch):
    # the cloudx CLI authenticates with X-Auth-Token, which the shared middleware accepts everywhere
    assert eng().caller_authorization(Req(**{"X-Auth-Token": " abc "}).headers) == "Bearer abc"


def test_no_credentials_gives_none(orch):
    assert eng().caller_authorization({}) is None


def test_stack_and_scaling_calls_accept_an_x_auth_token_only_request(orch):
    assert orch.stacks._api(Req(**{"X-Auth-Token": "abc"})).headers == {"Authorization": "Bearer abc"}
    with pytest.raises(HTTPException) as e:
        orch.stacks._api(Req())
    assert e.value.status_code == 401
    with pytest.raises(HTTPException) as e:
        orch._compute(Req(), "POST", "/servers", {})
    assert e.value.status_code == 401


def test_manual_scale_calls_compute_with_the_callers_token(orch, monkeypatch):
    seen = {}

    class FakeApi:
        def __init__(self, authorization):
            seen["authorization"] = authorization

        def call(self, service, method, path, body=None, ok=()):
            seen["call"] = (service, method, path)
            return {"id": "srv-1"}

    monkeypatch.setattr(eng(), "Api", FakeApi)
    assert orch._compute(Req(**{"X-Auth-Token": "abc"}), "POST", "/servers", {"name": "x"}) == {"id": "srv-1"}
    assert seen == {"authorization": "Bearer abc", "call": ("compute", "POST", "/servers")}
