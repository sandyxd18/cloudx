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


# --- the k8s engine worker makes a cluster's group as the cluster's project, with the internal token ----------------

def _as_cluster(monkeypatch, project="proj-9"):
    """Headers of a service call made on behalf of a member of `project` (shared.internal.on_behalf_headers)."""
    from shared.internal import on_behalf_headers

    monkeypatch.setenv("INTERNAL_TOKEN", "t" * 32)
    return on_behalf_headers({"sub": "k8s-cluster:cls-1", "username": "k8s-engine", "role": "member", "roles": ["member"],
                              "project_id": project, "scope_type": "project"})


@pytest.fixture
def internal_api(networking, monkeypatch):
    """No user override: a call without a user token or the internal token must be refused."""
    networking.ovn_cli.ovn = lambda *a, check=True, **k: "name : pg_x" if a[:1] == ("--format=list",) else ""
    networking.client = TestClient(networking.app)
    return networking


def test_an_internal_caller_makes_uses_and_deletes_a_group_as_the_project_it_states(internal_api, monkeypatch):
    h = _as_cluster(monkeypatch)
    client = internal_api.client

    made = client.post("/security-groups", json={"name": "k8s-cls-1"}, headers=h)
    assert made.status_code == 201, made.text
    assert made.json()["project_id"] == "proj-9"                       # the stated project, not the synthetic admin's
    gid = made.json()["id"]

    rule = client.post(f"/security-groups/{gid}/rules", headers=h,
                       json={"direction": "ingress", "protocol": "any", "remote_group_id": gid})
    assert rule.status_code == 201, rule.text
    assert client.get("/security-groups/k8s-cls-1", headers=h).json()["id"] == gid

    other = _as_cluster(monkeypatch, project="proj-other")             # another project cannot see or delete it
    assert client.get("/security-groups/k8s-cls-1", headers=other).status_code == 404
    assert client.delete(f"/security-groups/{gid}", headers=other).status_code == 404

    assert client.delete(f"/security-groups/{gid}", headers=h).status_code == 200


def test_without_a_user_token_or_the_internal_token_the_group_routes_stay_closed(internal_api, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "t" * 32)
    client = internal_api.client

    assert client.post("/security-groups", json={"name": "x"}).status_code == 401
    assert client.post("/security-groups", json={"name": "x"}, headers={"X-Internal-Token": "wrong"}).status_code == 401
    assert client.delete("/security-groups/x").status_code == 401
    assert client.post("/security-groups/x/rules", json={"direction": "ingress"}).status_code == 401
