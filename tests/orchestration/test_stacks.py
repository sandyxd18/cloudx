import importlib

import pytest
from fastapi import HTTPException

from services.orchestration.app import engine as eng

A = {"project_id": "p1", "role": "member"}
B = {"project_id": "p2", "role": "member"}

TPL = """
parameters:
  cidr: {type: string, default: 10.9.0.0/24}
  count: {type: number, default: 1}
resources:
  srv:
    type: OS::Nova::Server
    properties:
      image: cirros
      flavor: tiny
      networks: [{network: {get_resource: net}}]
  net: {type: OS::Neutron::Net}
  sub:
    type: OS::Neutron::Subnet
    properties: {network: {get_resource: net}, cidr: {get_param: cidr}}
outputs:
  ip: {value: {get_attr: [srv, ip_address]}}
"""


class FakeApi:
    """Records calls; `fail_on` makes the first matching POST path fail."""

    def __init__(self, fail_on=None):
        self.calls, self.fail_on, self.n = [], fail_on, 0

    def call(self, svc, method, path, body=None, ok=(200, 201, 202, 204)):
        self.calls.append((method, path))
        if method == "POST" and self.fail_on and path.startswith(self.fail_on):
            raise eng.ResourceFailure(f"POST {path} -> 500 boom")
        if method == "GET":
            return None if 404 in ok else {"status": "ACTIVE" if "servers" in path else "available", "ip_address": "10.9.0.5"}
        self.n += 1
        return {"id": f"id{self.n}", "name": (body or {}).get("name"), "floating_ip_address": None}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(eng, "POLL", 0)


def test_order_params_and_errors():
    t = eng.load(TPL)
    order = eng.order(t)
    assert order.index("net") < order.index("sub") and order.index("net") < order.index("srv")
    assert eng.merge_params(t, {"count": "3"}) == {"cidr": "10.9.0.0/24", "count": 3}
    with pytest.raises(eng.TemplateError):
        eng.merge_params(t, {"nope": 1})
    with pytest.raises(eng.TemplateError):
        eng.load("resources: {x: {type: OS::Bogus::Thing}}")
    with pytest.raises(eng.TemplateError, match="cycle"):
        eng.order(eng.load("resources: {a: {type: OS::Neutron::Net, depends_on: [b]}, b: {type: OS::Neutron::Net, depends_on: [a]}}"))
    with pytest.raises(eng.TemplateError, match="unknown resource"):
        eng.order(eng.load("resources: {a: {type: OS::Neutron::Net, depends_on: [zz]}}"))
    assert eng.preview(t, eng.merge_params(t, {}))[0]["name"] == "net"


def test_create_rolls_back_in_reverse_order():
    t, events = eng.load(TPL), []
    rec = lambda name, rtype, status, reason=None, **k: events.append((name, status))  # noqa: E731
    api = FakeApi(fail_on="/subnets")
    err, _ = eng.create(t, eng.merge_params(t, {}), "s", api, rec)
    assert err.startswith("sub:")
    deleted = [n for n, s in events if s == "DELETE_COMPLETE"]
    assert deleted == ["srv", "net"]  # reverse of creation; the failed subnet was never built
    assert ("sub", "CREATE_FAILED") in events


@pytest.fixture
def stk(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'o.sqlite'}")
    import services.orchestration.app.main as m

    m = importlib.reload(m)
    s = importlib.reload(m.stacks)
    m.Base.metadata.create_all(bind=m.engine)
    monkeypatch.setattr(s, "_run", lambda fn: fn())
    return m, s


class Req:
    def __init__(self, auth="Bearer x"):
        self.headers = {"Authorization": auth} if auth else {}


def _patch_api(monkeypatch, s, api):  # NB: tests share one session with the inline job, so call db.expire_all() after async actions
    monkeypatch.setattr(s, "_api", lambda request: api)


def test_stack_lifecycle(stk, monkeypatch):
    m, s = stk
    api = FakeApi()
    _patch_api(monkeypatch, s, api)
    with m.SessionLocal() as db:
        s.create_stack(A, s.StackIn(name="web", template=TPL), Req(), db)
        got = s.show_stack(A, "web", db)["stack"]
        assert got["stack_status"] == "CREATE_COMPLETE" and got["outputs"] == {"ip": "10.9.0.5"}
        assert len(s.stack_resources(A, "web", db)["resources"]) == 3
        assert s.stack_events(A, "web", db)["events"][-1]["resource_status"] == "CREATE_COMPLETE"
        with pytest.raises(HTTPException) as e:
            s.create_stack(A, s.StackIn(name="web", template=TPL), Req(), db)
        assert e.value.status_code == 409
        for call in (lambda: s.show_stack(B, "web", db), lambda: s.delete_stack(B, "web", Req(), db)):
            with pytest.raises(HTTPException) as e:
                call()
            assert e.value.status_code == 404
        assert s.list_stacks(B, db)["stacks"] == []

        # update: add a volume, then drop it again; changing an existing resource is refused
        more = TPL.replace("outputs:", "  vol: {type: OS::Cinder::Volume, properties: {size: 1}}\noutputs:")
        s.update_stack(A, "web", s.StackUpdate(template=more), Req(), db); db.expire_all()
        assert {r["resource_name"] for r in s.stack_resources(A, "web", db)["resources"]} == {"srv", "net", "sub", "vol"}
        s.update_stack(A, "web", s.StackUpdate(template=TPL), Req(), db); db.expire_all()
        assert len(s.stack_resources(A, "web", db)["resources"]) == 3
        with pytest.raises(HTTPException) as e:
            s.update_stack(A, "web", s.StackUpdate(template=TPL, parameters={"cidr": "10.8.0.0/24"}), Req(), db)
        assert e.value.status_code == 400

        s.stack_action(A, "web", Req(), db, {"suspend": None}); db.expire_all()
        assert s.show_stack(A, "web", db)["stack"]["stack_status"] == "SUSPEND_COMPLETE"
        assert ("POST", "/servers/id1/suspend") in api.calls or any(c[1].endswith("/suspend") for c in api.calls)
        s.stack_action(A, "web", Req(), db, {"resume": None}); db.expire_all()

        s.delete_stack(A, "web", Req(), db); db.expire_all()
        assert s.list_stacks(A, db)["stacks"] == []
        assert sum(1 for c in api.calls if c[0] == "DELETE") >= 3


def test_failed_stack_rolls_back_and_bad_template_is_400(stk, monkeypatch):
    m, s = stk
    _patch_api(monkeypatch, s, FakeApi(fail_on="/subnets"))
    with m.SessionLocal() as db:
        s.create_stack(A, s.StackIn(name="bad", template=TPL), Req(), db)
        st = s.show_stack(A, "bad", db)["stack"]
        assert st["stack_status"] == "ROLLBACK_COMPLETE" and "sub" in st["stack_status_reason"]
        with pytest.raises(HTTPException) as e:
            s.create_stack(A, s.StackIn(name="x", template="resources: {}"), Req(), db)
        assert e.value.status_code == 400


def test_missing_authorization_is_401(stk):
    with pytest.raises(HTTPException) as e:
        stk[1]._api(Req(auth=None))
    assert e.value.status_code == 401


def test_manual_scale_really_calls_compute(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'g.sqlite'}")
    import services.orchestration.app.main as m

    m = importlib.reload(m)
    importlib.reload(m.stacks)
    m.Base.metadata.create_all(bind=m.engine)
    calls = []
    monkeypatch.setattr(m, "_compute", lambda req, method, path, body=None: calls.append((method, path)) or {"id": "srv-1"})
    with m.SessionLocal() as db:
        g = m.create_scaling_group(A, m.ScalingGroupIn(name="g", min_instances=0, image_id="i", flavor_id="f", network_id="n"), db)
        assert m.manual_scale_out(A, g.id, db).server_id == "srv-1"
        m.manual_scale_in(A, g.id, db)
    assert calls == [("POST", "/servers"), ("DELETE", "/servers/srv-1")]
