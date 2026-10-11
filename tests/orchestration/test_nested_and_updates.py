import textwrap

import pytest
from fastapi import HTTPException

from tests.orchestration.test_stacks import A, FakeApi, Req, _patch_api


def test_nested_stack_create_output_delete_cascade(stk, monkeypatch):
    m, s = stk
    api = FakeApi()
    _patch_api(monkeypatch, s, api)

    child_template = """
resources:
  child_net:
    type: OS::Neutron::Net
outputs:
  net_id:
    value: {get_attr: [child_net, name]}
"""

    parent_template = f"""
resources:
  child:
    type: OS::Heat::Stack
    properties:
      template: |
{textwrap.indent(child_template.strip(), "        ")}
  parent_srv:
    type: OS::Nova::Server
    properties:
      image: cirros
      flavor: tiny
      network: {{get_attr: [child, outputs, net_id]}}
outputs:
  srv_net:
    value: {{get_attr: [child, outputs, net_id]}}
"""

    with m.SessionLocal() as db:
        res = s.create_stack(A, s.StackIn(name="parent_stack", template=parent_template), Req(), db)
        parent_id = res["id"]
        db.expire_all()

        parent_st = s.show_stack(A, "parent_stack", db)["stack"]
        assert parent_st["stack_status"] == "CREATE_COMPLETE"
        assert parent_st["outputs"]["srv_net"] == "parent_stack-child-child_net"

        # Child stack is hidden from default list
        stacks_list = s.list_stacks(A, db)["stacks"]
        assert len(stacks_list) == 1
        assert stacks_list[0]["id"] == parent_id

        # Child stack is reachable by id directly
        child_res = s.stack_resources(A, "parent_stack", db)["resources"]
        nested_res = next(r for r in child_res if r["resource_type"] == "OS::Heat::Stack")
        child_stack_id = nested_res["physical_resource_id"]

        child_st = s.show_stack(A, child_stack_id, db)["stack"]
        assert child_st["id"] == child_stack_id
        assert child_st["stack_status"] == "CREATE_COMPLETE"

        # Delete parent stack -> cascade delete child stack
        s.delete_stack(A, "parent_stack", Req(), db)
        db.expire_all()

        assert s.list_stacks(A, db)["stacks"] == []
        with pytest.raises(HTTPException) as exc_info:
            s.show_stack(A, child_stack_id, db)
        assert exc_info.value.status_code == 404


def test_nested_stack_child_failure_rolls_back(stk, monkeypatch):
    m, s = stk
    _patch_api(monkeypatch, s, FakeApi(fail_on="/subnets"))

    bad_child_template = """
resources:
  sub:
    type: OS::Neutron::Subnet
    properties:
      cidr: 10.0.0.0/24
      network: net1
"""

    parent_template = f"""
resources:
  child:
    type: OS::Heat::Stack
    properties:
      template: |
{bad_child_template.strip()}
"""

    with m.SessionLocal() as db:
        s.create_stack(A, s.StackIn(name="bad_nested", template=parent_template), Req(), db)
        db.expire_all()

        parent_st = s.show_stack(A, "bad_nested", db)["stack"]
        assert parent_st["stack_status"] == "ROLLBACK_COMPLETE"
        assert "sub" in parent_st["stack_status_reason"]


def test_nested_stack_depth_limit(stk, monkeypatch):
    m, s = stk
    _patch_api(monkeypatch, s, FakeApi())

    # Level 4 nesting (Root -> Level2 -> Level3 -> Level4)
    lvl4 = {"resources": {"net": {"type": "OS::Neutron::Net"}}}
    lvl3 = {"resources": {"c4": {"type": "OS::Heat::Stack", "properties": {"template": lvl4}}}}
    lvl2 = {"resources": {"c3": {"type": "OS::Heat::Stack", "properties": {"template": lvl3}}}}
    lvl1 = {"resources": {"c2": {"type": "OS::Heat::Stack", "properties": {"template": lvl2}}}}

    with m.SessionLocal() as db:
        s.create_stack(A, s.StackIn(name="depth_test", template=lvl1), Req(), db)
        db.expire_all()

        st = s.show_stack(A, "depth_test", db)["stack"]
        assert st["stack_status"] == "ROLLBACK_COMPLETE"
        assert "nested stack depth limit exceeded" in st["stack_status_reason"]


def test_inplace_vs_replace_matrix(stk, monkeypatch):
    m, s = stk
    api = FakeApi()
    _patch_api(monkeypatch, s, api)

    tpl_v1 = """
resources:
  srv:
    type: OS::Nova::Server
    properties:
      image: cirros
      flavor: tiny
      network: net1
      user_data: echo hello
  vol:
    type: OS::Cinder::Volume
    properties:
      name: v1
      size: 5
  sg:
    type: OS::Neutron::SecurityGroup
    properties:
      name: sg1
      rules: [{direction: ingress, protocol: tcp, port_range_min: 80}]
"""

    with m.SessionLocal() as db:
        s.create_stack(A, s.StackIn(name="inplace_stack", template=tpl_v1), Req(), db)
        db.expire_all()

        res_v1 = {r["resource_name"]: r["physical_resource_id"] for r in s.stack_resources(A, "inplace_stack", db)["resources"]}

        # Update in-place: server user_data & flavor resize, volume extend, security group rules
        tpl_v2 = tpl_v1.replace("user_data: echo hello", "user_data: echo world").replace("flavor: tiny", "flavor: medium").replace("size: 5", "size: 10").replace("port_range_min: 80", "port_range_min: 443")

        s.update_stack(A, "inplace_stack", s.StackUpdate(template=tpl_v2), Req(), db)
        db.expire_all()

        st = s.show_stack(A, "inplace_stack", db)["stack"]
        assert st["stack_status"] == "UPDATE_COMPLETE"

        res_v2 = {r["resource_name"]: r["physical_resource_id"] for r in s.stack_resources(A, "inplace_stack", db)["resources"]}

        # Physical IDs remained identical for in-place updates
        assert res_v1["srv"] == res_v2["srv"]
        assert res_v1["vol"] == res_v2["vol"]
        assert res_v1["sg"] == res_v2["sg"]

        # Replace update: server image change forces replacement (new physical_id)
        tpl_v3 = tpl_v2.replace("image: cirros", "image: ubuntu")
        s.update_stack(A, "inplace_stack", s.StackUpdate(template=tpl_v3), Req(), db)
        db.expire_all()

        res_v3 = {r["resource_name"]: r["physical_resource_id"] for r in s.stack_resources(A, "inplace_stack", db)["resources"]}
        assert res_v3["srv"] != res_v2["srv"]  # Server was replaced
