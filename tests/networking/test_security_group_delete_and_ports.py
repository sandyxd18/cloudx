"""Security group deletion must really remove the OVN port group, and rules must keep their ports.

`ovn-nbctl pg-del` has no --if-exists option, so deleting a group used to fail inside OVN, the error was swallowed and
the port group (with its ACLs) stayed behind. The CLI also sends Neutron-style port_range_min/max, which the API
ignored, so a rule meant for one port opened every port of the protocol.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api(networking):
    from shared.auth_middleware import require_auth

    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    ovn_calls = []

    def fake_ovn(*args, check=True, **kwargs):
        ovn_calls.append((args, check))
        # `find port_group` answers like a port group that exists
        return "name : pg_x" if args[:1] == ("--format=list",) else ""

    networking.ovn_cli.ovn = fake_ovn
    networking.ovn_calls = ovn_calls
    networking.client = TestClient(networking.app)
    return networking


def make_group(api):
    res = api.client.post("/security-groups", json={"name": "web"})
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def test_deleting_a_group_removes_its_port_group_with_a_pg_del_ovn_accepts(api):
    group_id = make_group(api)
    api.ovn_calls.clear()

    res = api.client.delete(f"/security-groups/{group_id}")

    assert res.status_code == 200
    pg_del = [(args, check) for args, check in api.ovn_calls if "pg-del" in args]
    assert len(pg_del) == 1
    args, check = pg_del[0]
    assert "--if-exists" not in args          # pg-del rejects it: "'pg-del' command has no '--if-exists' option"
    assert check is True                       # a failure must not be swallowed and leave an orphan behind


def test_deleting_a_group_whose_port_group_is_already_gone_still_succeeds(api):
    group_id = make_group(api)
    api.ovn_calls.clear()
    api.ovn_cli.ovn = lambda *args, check=True, **kwargs: api.ovn_calls.append((args, check)) or ""   # find: no match

    res = api.client.delete(f"/security-groups/{group_id}")

    assert res.status_code == 200
    assert not any("pg-del" in args for args, _ in api.ovn_calls)


@pytest.mark.parametrize("payload", [
    {"port_min": 8080, "port_max": 8080},
    {"port_range_min": 8080, "port_range_max": 8080},
])
def test_rule_port_is_stored_whichever_field_names_the_client_uses(api, payload):
    group_id = make_group(api)

    res = api.client.post(f"/security-groups/{group_id}/rules",
                          json={"direction": "ingress", "protocol": "tcp", "remote_ip_prefix": "0.0.0.0/0", **payload})

    assert res.status_code == 201, res.text
    body = res.json()
    assert body["port_min"] == "8080" and body["port_max"] == "8080"


def test_rule_port_range_is_stored_from_neutron_style_names(api):
    group_id = make_group(api)

    res = api.client.post(f"/security-groups/{group_id}/rules",
                          json={"direction": "ingress", "protocol": "tcp", "port_range_min": 8000, "port_range_max": 8100})

    assert res.status_code == 201, res.text
    assert (res.json()["port_min"], res.json()["port_max"]) == ("8000", "8100")
