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

def test_trunk_vpn_and_peering_are_project_scoped(api):
    c = api.client
    assert c.post("/trunks", json={"name": "t", "port_id": "p-b"}).status_code == 404          # foreign parent port
    assert c.post("/routers/rtr-b/peering", json={"name": "x", "accepter_router_id": "r-a"}).status_code == 404
    assert c.post("/vpn/ikepolicies", json={"name": "ike"}).status_code == 201
    pid = c.get("/vpn/ikepolicies").json()[0]["id"]
    api.user["project_id"] = "proj-b"
    assert c.get(f"/vpn/ikepolicies/{pid}").status_code == 404
    assert c.delete(f"/vpn/ikepolicies/{pid}").status_code == 404
    assert c.get("/vpn/ikepolicies").json() == []


def test_same_name_in_different_projects_succeeds(api):
    c = api.client
    api.user["project_id"] = "proj-a"
    res_a = c.post("/networks", json={"name": "same-name-net"})
    assert res_a.status_code == 201

    api.user["project_id"] = "proj-b"
    res_b = c.post("/networks", json={"name": "same-name-net"})
    assert res_b.status_code == 201


def test_same_name_in_same_project_fails_409(api):
    c = api.client
    api.user["project_id"] = "proj-a"
    res1 = c.post("/routers", json={"name": "duplicate-rtr"})
    assert res1.status_code == 201
    res2 = c.post("/routers", json={"name": "duplicate-rtr"})
    assert res2.status_code == 409


def test_lookup_by_name_scoped_to_project(api):
    c = api.client
    api.user["project_id"] = "proj-a"
    c.post("/networks", json={"name": "scoped-net"})

    api.user["project_id"] = "proj-b"
    c.post("/networks", json={"name": "scoped-net"})

    api.user["project_id"] = "proj-a"
    net_a = c.get("/networks/scoped-net").json()
    assert net_a["project_id"] == "proj-a"

    api.user["project_id"] = "proj-b"
    net_b = c.get("/networks/scoped-net").json()
    assert net_b["project_id"] == "proj-b"


def test_admin_lookup_ambiguous_name_returns_409(api):
    c = api.client
    api.user["project_id"] = "proj-a"
    c.post("/security-groups", json={"name": "ambig-sg"})

    api.user["project_id"] = "proj-b"
    c.post("/security-groups", json={"name": "ambig-sg"})

    api.user["role"] = "admin"
    api.user["project_id"] = None
    res = c.get("/security-groups/ambig-sg")
    assert res.status_code == 409


def test_router_project_id_and_peering_bgp_ownership(api):
    c = api.client
    api.user["role"] = "member"
    api.user["project_id"] = "proj-a"
    r_a = c.post("/routers", json={"name": "rtr-proj-a"}).json()
    assert r_a["project_id"] == "proj-a"

    api.user["project_id"] = "proj-b"
    r_b = c.post("/routers", json={"name": "rtr-proj-b"}).json()
    assert r_b["project_id"] == "proj-b"

    api.user["project_id"] = "proj-a"
    res_peer = c.post(f"/routers/{r_a['id']}/peering", json={"name": "p1", "accepter_router_id": r_b["id"]})
    assert res_peer.status_code == 404

    res_bgp = c.post("/bgp-peers", json={"name": "bgp1", "router_id": r_b["id"], "peer_ip": "1.1.1.1", "remote_as": 65000, "local_as": 65001})
    assert res_bgp.status_code == 404
