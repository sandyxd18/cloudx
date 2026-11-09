import pytest

from services.orchestration.app import engine as eng
from tests.orchestration.test_stacks import FakeApi


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(eng, "POLL", 0)


BAD = ["..", ".", ""]


@pytest.mark.parametrize("bad", BAD)
def test_interface_with_unnamed_router_or_subnet_fails_the_resource(bad):
    for p in ({"router": bad, "subnet": "s1"}, {"router": "r1", "subnet": bad}):
        api = FakeApi()
        with pytest.raises(eng.ResourceFailure, match="not found"):
            eng._iface_c(api, p)
        assert api.calls == []


@pytest.mark.parametrize("bad", BAD)
def test_floating_ip_association_with_unnamed_id_fails_the_resource(bad):
    api = FakeApi()
    with pytest.raises(eng.ResourceFailure, match="not found"):
        eng._fipassoc_c(api, {"floatingip_id": bad, "port_id": "p1"})
    with pytest.raises(eng.ResourceFailure, match="not found"):
        eng._fipassoc_u(api, "x", {}, {"floatingip_id": bad, "port_id": "p1"})
    assert api.calls == []


def test_references_with_slash_or_query_are_one_segment():
    api = FakeApi()
    pid, _ = eng._iface_c(api, {"router": "r/1", "subnet": "s?1"})
    assert api.calls == [("POST", "/routers/r%2F1/interfaces")]
    assert pid == "r%2F1/s%3F1"
    eng._iface_d(api, pid)
    assert api.calls[-1] == ("DELETE", "/routers/r%2F1/interfaces/s%3F1")
    api.calls.clear()
    eng._fipassoc_c(api, {"floatingip_id": "f?1", "port_id": "p1"})
    assert api.calls == [("POST", "/floating-ips/f%3F1/associate")]


def test_plain_references_are_sent_as_before():
    api = FakeApi()
    pid, _ = eng._iface_c(api, {"router": "r1", "subnet": "s1"})
    assert pid == "r1/s1" and api.calls == [("POST", "/routers/r1/interfaces")]
    eng._fipassoc_c(api, {"floatingip_id": "f1", "port_id": "p1"})
    assert api.calls[-1] == ("POST", "/floating-ips/f1/associate")


def test_refused_reference_is_recorded_as_a_failed_resource_and_the_stack_goes_on_to_roll_back():
    tpl = eng.load("""
resources:
  net: {type: OS::Neutron::Net}
  assoc:
    type: OS::Neutron::FloatingIPAssociation
    properties: {floatingip_id: "..", port_id: p1}
""")
    seen = []
    api = FakeApi()
    err, _ = eng.create(tpl, {}, "st", api, lambda *a, **k: seen.append((a, k)))
    assert err and "'..' not found" in err
    assert any(a[1] == "OS::Neutron::FloatingIPAssociation" and a[2] == "CREATE_FAILED" for a, _ in seen)
    assert not [c for c in api.calls if "floating-ips" in c[1]]
