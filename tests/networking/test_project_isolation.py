"""Networking resources belong to a project: other projects get 404, admin sees all, legacy NULL rows stay visible.
Also: IPv6 security-group rules produce ip6 ACL matches."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api(networking):
    from shared.auth_middleware import require_auth

    user = {"project_id": "proj-a", "role": "member"}
    networking.user = user
    networking.app.dependency_overrides[require_auth] = lambda: user
    networking.ovn_cli.ovn = lambda *args, **kwargs: ""
    networking.client = TestClient(networking.app)
    with networking.db.SessionLocal() as db:
        m = networking.models
        db.add(m.Network(id="n-a", name="net-a", project_id="proj-a"))
        db.add(m.Network(id="n-b", name="net-b", project_id="proj-b"))
        db.add(m.Network(id="n-old", name="net-old"))
        db.add(m.Router(id="r-a", name="rtr-a", project_id="proj-a"))
        db.add(m.Router(id="r-b", name="rtr-b", project_id="proj-b"))
        db.add(m.Port(id="p-b", name="port-b", network_id="n-b", mac_address="02:00:00:00:00:09", project_id="proj-b"))
        db.commit()
    return networking


def ids(res):
    return {i["id"] for i in res.json()["items"]}


def test_member_sees_only_own_and_legacy(api):
    assert ids(api.client.get("/networks")) == {"n-a", "n-old"}
    assert ids(api.client.get("/routers")) == {"r-a"}
    assert ids(api.client.get("/ports")) == set()


def test_other_project_is_404_not_403(api):
    assert api.client.get("/routers/r-b").status_code == 404
    assert api.client.delete("/routers/r-b").status_code == 404
    assert api.client.get("/ports/port-b").status_code == 404
    assert api.client.post("/subnets", json={"name": "s", "network": "net-b", "cidr": "10.9.0.0/24",
                                              "gateway_ip": "10.9.0.1"}).status_code == 404


def test_admin_sees_everything(api):
    api.user["role"] = "admin"
    assert ids(api.client.get("/routers")) == {"r-a", "r-b"}


def test_created_resources_record_the_owner(api):
    assert api.client.post("/routers", json={"name": "new"}).status_code == 201
    assert api.client.get("/routers/new").json()["project_id"] == "proj-a"
    api.user["project_id"] = "proj-b"
    assert api.client.get("/routers/new").status_code == 404


def test_ipv6_rule_uses_ip6_match(api):
    from types import SimpleNamespace as R

    rule = R(direction="ingress", ethertype="IPv6", protocol="icmp", port_min=None, port_max=None,
             remote_ip_prefix="fd00::/64", remote_group_id=None)
    m = api.ovn_cli.acl_match("sg1", rule)
    assert "ip6" in m and "icmp6" in m and "ip6.src == fd00::/64" in m and "ip4" not in m
    rule.ethertype = "IPv4"
    rule.remote_ip_prefix = "10.0.0.0/8"
    assert "ip4.src" in api.ovn_cli.acl_match("sg1", rule) and "ip6" not in api.ovn_cli.acl_match("sg1", rule)


def test_ipv6_rule_with_ipv4_prefix_rejected(api):
    with api.db.SessionLocal() as db:
        db.add(api.models.SecurityGroup(id="sg-a", name="sga", project_id="proj-a"))
        db.commit()
    res = api.client.post("/security-groups/sg-a/rules", json={"direction": "ingress", "protocol": "tcp",
                                                                "ethertype": "IPv6", "remote_ip_prefix": "10.0.0.0/8"})
    assert res.status_code == 400
