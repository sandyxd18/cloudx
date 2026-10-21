"""Heat-like stack engine: HOT template parsing, dependency order, resource handlers.

No database here. `create`/`destroy` report progress through a `record(name, type, status, reason, pid, props, attrs)`
callback, and talk to the other services only through `Api.call`, so tests can fake both.
"""
from __future__ import annotations

import graphlib
import ipaddress
import json
import time
import uuid

import requests
import yaml
from shared import catalog
from sqlalchemy import select

POLL = 3  # seconds between status polls (tests set 0)
SERVICES = {"net": "network", "compute": "compute", "block": "volume"}


class TemplateError(ValueError):
    pass


class ResourceFailure(RuntimeError):
    pass


class Api:
    """The caller's own token is forwarded, so stacks obey the caller's project and quota."""

    def __init__(self, authorization: str):
        self.headers = {"Authorization": authorization}

    def call(self, svc, method, path, body=None, ok=(200, 201, 202, 204)):
        r = requests.request(method, catalog.url(SERVICES[svc]) + path, headers=self.headers, json=body, timeout=60)
        if r.status_code == 404 and 404 in ok:
            return None
        if r.status_code not in ok:
            raise ResourceFailure(f"{method} {path} -> {r.status_code} {r.text[:150]}")
        return r.json() if r.content else {}


# ---- template -------------------------------------------------------------------------------------------------

def load(template) -> dict:
    if isinstance(template, str):
        try:
            template = yaml.safe_load(template)  # JSON is valid YAML
        except yaml.YAMLError as exc:
            raise TemplateError(f"template is not valid YAML/JSON: {exc}") from exc
    if not isinstance(template, dict) or not isinstance(template.get("resources"), dict) or not template["resources"]:
        raise TemplateError("template needs a non-empty 'resources' mapping")
    for name, res in template["resources"].items():
        if not isinstance(res, dict) or res.get("type") not in TYPES:
            raise TemplateError(f"resource '{name}': unsupported type {res.get('type') if isinstance(res, dict) else res!r}")
    return template


def merge_params(template: dict, given: dict | None) -> dict:
    given, out = given or {}, {}
    for name, spec in (template.get("parameters") or {}).items():
        if name in given:
            v = given[name]
        elif "default" in spec:
            v = spec["default"]
        else:
            raise TemplateError(f"parameter '{name}' is required")
        t = spec.get("type", "string")
        try:
            v = {"number": float, "boolean": lambda x: x if isinstance(x, bool) else str(x).lower() == "true"}.get(t, lambda x: x)(v)
        except (TypeError, ValueError) as exc:
            raise TemplateError(f"parameter '{name}' must be {t}") from exc
        if t == "number" and v == int(v):
            v = int(v)
        if spec.get("allowed_values") and v not in spec["allowed_values"]:
            raise TemplateError(f"parameter '{name}' must be one of {spec['allowed_values']}")
        out[name] = v
    unknown = set(given) - set(out)
    if unknown:
        raise TemplateError(f"unknown parameter(s): {sorted(unknown)}")
    return out


def _refs(v) -> set[str]:
    if isinstance(v, dict):
        if "get_resource" in v:
            return {v["get_resource"]}
        if "get_attr" in v:
            return {v["get_attr"][0]}
        return set().union(*map(_refs, v.values())) if v else set()
    if isinstance(v, list):
        return set().union(*map(_refs, v)) if v else set()
    return set()


def order(template: dict) -> list[str]:
    res = template["resources"]
    deps = {}
    for name, r in res.items():
        d = _refs(r.get("properties", {})) | set(r.get("depends_on") or [])
        if d - set(res):
            raise TemplateError(f"resource '{name}' refers to unknown resource(s) {sorted(d - set(res))}")
        deps[name] = d
    try:
        return list(graphlib.TopologicalSorter(deps).static_order())
    except graphlib.CycleError as exc:
        raise TemplateError(f"dependency cycle: {exc.args[1]}") from exc


def resolve(v, params: dict, state: dict, preview: bool = False):
    if isinstance(v, dict):
        if "get_param" in v:
            name = v["get_param"]
            if name not in params:
                raise TemplateError(f"get_param: unknown parameter '{name}'")
            return params[name]
        if "get_resource" in v:
            return f"<{v['get_resource']}>" if preview else state[v["get_resource"]]["id"]
        if "get_attr" in v:
            attr_path = v["get_attr"]
            if isinstance(attr_path, list):
                n = attr_path[0]
                if preview:
                    return f"<{n}.{'.'.join(attr_path[1:])}>"
                if n not in state:
                    raise ResourceFailure(f"resource '{n}' not found in state")
                if len(attr_path) > 2 and attr_path[1] == "outputs":
                    out_name = attr_path[2]
                    outs = state[n].get("outputs", {})
                    if isinstance(outs, str):
                        try:
                            outs = json.loads(outs)
                        except ValueError:  # best-effort: outputs that are not JSON simply have no named attribute
                            outs = {}
                    if isinstance(outs, dict) and out_name in outs:
                        return outs[out_name]
                    if out_name in state[n]:
                        return state[n][out_name]
                    raise ResourceFailure(f"resource '{n}' has no output attribute '{out_name}'")
                attr = attr_path[1]
                if attr in state[n]:
                    return state[n][attr]
                outs = state[n].get("outputs", {})
                if isinstance(outs, str):
                    try:
                        outs = json.loads(outs)
                    except ValueError:  # best-effort: outputs that are not JSON simply have no named attribute
                        outs = {}
                if isinstance(outs, dict) and attr in outs:
                    return outs[attr]
                raise ResourceFailure(f"resource '{n}' has no attribute '{attr}'")
        return {k: resolve(x, params, state, preview) for k, x in v.items()}
    if isinstance(v, list):
        return [resolve(x, params, state, preview) for x in v]
    return v


def outputs(template: dict, params: dict, state: dict) -> dict:
    out = {}
    for name, spec in (template.get("outputs") or {}).items():
        try:
            out[name] = resolve(spec.get("value"), params, state)
        except (KeyError, ResourceFailure, TemplateError) as exc:
            out[name] = f"<error: {exc}>"
    return out


def preview(template: dict, params: dict) -> list[dict]:
    return [{"name": n, "type": template["resources"][n]["type"],
             "properties": resolve(template["resources"][n].get("properties", {}), params, {}, preview=True)}
            for n in order(template)]


# ---- resource handlers: create(api, props) -> (physical_id, attrs); delete(api, physical_id) -----------------

def _wait(api, svc, path, want, bad=("ERROR", "error")):
    for _ in range(100):
        r = api.call(svc, "GET", path)
        if r.get("status") in want:
            return r
        if r.get("status") in bad:
            raise ResourceFailure(f"{path} went to {r.get('status')}: {r.get('error_message') or ''}".strip())
        time.sleep(POLL)
    raise ResourceFailure(f"timed out waiting for {path}")


def _gone(api, svc, path):
    for _ in range(100):
        r = api.call(svc, "GET", path, ok=(200, 404))
        if r is None or str(r.get("status", "")).lower() == "deleted":  # compute soft-deletes
            return
        time.sleep(POLL)
    raise ResourceFailure(f"timed out waiting for {path} to be deleted")


def _pick(p, *keys):
    return {k: p[k] for k in keys if p.get(k) is not None}


def _net_c(api, p):
    r = api.call("net", "POST", "/networks", _pick(p, "name", "mtu"))
    return r["id"], {"name": r.get("name")}


def _subnet_c(api, p):
    gw = p.get("gateway_ip") or str(next(ipaddress.ip_network(p["cidr"]).hosts()))
    r = api.call("net", "POST", "/subnets", {**_pick(p, "name", "network", "cidr", "enable_dhcp", "dns_nameservers"), "gateway_ip": gw})
    return r["id"], {"cidr": p["cidr"], "gateway_ip": gw}


def _router_c(api, p):
    r = api.call("net", "POST", "/routers", _pick(p, "name"))
    gw = (p.get("external_gateway_info") or {}).get("network")
    if gw:
        api.call("net", "POST", f"/routers/{r['id']}/gateway", {"external_network": gw})
    return r["id"], {}


def _router_d(api, rid):
    api.call("net", "DELETE", f"/routers/{rid}", ok=(200, 202, 204, 404))


def _iface_c(api, p):
    api.call("net", "POST", f"/routers/{p['router']}/interfaces", {"subnet": p["subnet"]})
    return f"{p['router']}/{p['subnet']}", {}


def _iface_d(api, pid):
    api.call("net", "DELETE", f"/routers/{pid.split('/')[0]}/interfaces/{pid.split('/')[1]}", ok=(200, 202, 204, 404))


def _fip_c(api, p):
    r = api.call("net", "POST", "/floating-ips", {"external_network": p["floating_network"], **_pick(p, "name")})
    return r["id"], {"floating_ip_address": r.get("floating_ip_address")}


def _fipassoc_c(api, p):
    key = "server" if p.get("server_id") else "port"
    api.call("net", "POST", f"/floating-ips/{p['floatingip_id']}/associate", {key: p.get("server_id") or p["port_id"]})
    return p["floatingip_id"], {}


def _fipassoc_d(api, fid):
    api.call("net", "POST", f"/floating-ips/{fid}/disassociate", {}, ok=(200, 202, 204, 404))


def _fipassoc_u(api, pid, old_p, new_p):
    key = "server" if new_p.get("server_id") else "port"
    api.call("net", "POST", f"/floating-ips/{new_p['floatingip_id']}/associate", {key: new_p.get("server_id") or new_p["port_id"]})
    return new_p["floatingip_id"], {}


def _sg_c(api, p):
    r = api.call("net", "POST", "/security-groups", _pick(p, "name", "description"))
    for rule in p.get("rules") or []:
        body = {"direction": rule.get("direction", "ingress"), "ethertype": rule.get("ethertype", "IPv4"),
                "protocol": rule.get("protocol", "any"), "port_min": rule.get("port_range_min"),
                "port_max": rule.get("port_range_max"), "remote_ip_prefix": rule.get("remote_ip_prefix"),
                "remote_group_id": rule.get("remote_group_id")}
        api.call("net", "POST", f"/security-groups/{r['id']}/rules", {k: v for k, v in body.items() if v is not None})
    return r["id"], {"name": r.get("name")}


def _sg_u(api, pid, old_p, new_p):
    if old_p.get("rules") != new_p.get("rules"):
        curr = api.call("net", "GET", f"/security-groups/{pid}/rules") or {}
        curr_rules = curr.get("items", [])
        for r in curr_rules:
            if "id" in r:
                api.call("net", "DELETE", f"/security-group-rules/{r['id']}", ok=(200, 202, 204, 404))
        for rule in new_p.get("rules") or []:
            body = {"direction": rule.get("direction", "ingress"), "ethertype": rule.get("ethertype", "IPv4"),
                    "protocol": rule.get("protocol", "any"), "port_min": rule.get("port_range_min"),
                    "port_max": rule.get("port_range_max"), "remote_ip_prefix": rule.get("remote_ip_prefix"),
                    "remote_group_id": rule.get("remote_group_id")}
            api.call("net", "POST", f"/security-groups/{pid}/rules", {k: v for k, v in body.items() if v is not None})
    return pid, {}


def _vol_c(api, p):
    r = api.call("block", "POST", "/volumes", {"name": p["name"], "size_gb": p["size"], **_pick(p, "image")})
    _wait(api, "block", f"/volumes/{r['id']}", ("available",))
    return r["id"], {"size": p["size"]}


def _vol_d(api, vid):
    api.call("block", "DELETE", f"/volumes/{vid}", ok=(200, 202, 204, 404))
    _gone(api, "block", f"/volumes/{vid}")


def _vol_u(api, pid, old_p, new_p):
    if old_p.get("size") != new_p.get("size"):
        old_size, new_size = old_p.get("size", 0), new_p.get("size", 0)
        if new_size > old_size:
            api.call("block", "POST", f"/volumes/{pid}/extend", {"new_size": new_size})
            _wait(api, "block", f"/volumes/{pid}", ("available",))
            return pid, {"size": new_size}
    return pid, {}


def _server_c(api, p):
    nets = [n.get("network") if isinstance(n, dict) else n for n in p.get("networks") or [] if n]
    nets = nets or ([p["network"]] if p.get("network") else [])
    if not nets:
        raise ResourceFailure("OS::Nova::Server needs 'networks' (or 'network')")
    body = {**_pick(p, "name", "image", "flavor", "key_name", "user_data", "security_groups", "availability_zone",
                    "volume_size"), "network": nets[0], "networks": nets[1:]}
    r = api.call("compute", "POST", "/servers", body)
    s = _wait(api, "compute", f"/servers/{r['id']}", ("ACTIVE",))
    return r["id"], {"name": s.get("name"), "ip_address": s.get("ip_address"), "first_address": s.get("ip_address")}


def _server_d(api, sid):
    api.call("compute", "DELETE", f"/servers/{sid}", ok=(200, 202, 204, 404))
    _gone(api, "compute", f"/servers/{sid}")


def _del(svc, path):
    return lambda api, pid: api.call(svc, "DELETE", f"{path}/{pid}", ok=(200, 202, 204, 404))


def _server_u(api, pid, old_p, new_p):
    if old_p.get("user_data") != new_p.get("user_data"):
        api.call("compute", "PUT", f"/servers/{pid}/user-data", {"user_data": new_p.get("user_data", "")})
    if old_p.get("flavor") != new_p.get("flavor") and new_p.get("flavor"):
        api.call("compute", "POST", f"/servers/{pid}/resize", {"flavor": new_p["flavor"]})
        _wait(api, "compute", f"/servers/{pid}", ("ACTIVE", "VERIFY_RESIZE"))
    return pid, {}


def _router_u(api, pid, old_p, new_p):
    if old_p.get("routes") != new_p.get("routes"):
        api.call("net", "PUT", f"/routers/{pid}", {"routes": new_p.get("routes", [])})
    return pid, {}


def _asg_c(api, p):
    size = int(p.get("size", 1))
    prefix = p.get("name", "asg")
    server_ids = []
    for i in range(size):
        sp = {**p, "name": f"{prefix}-{i}"}
        sid, _ = _server_c(api, sp)
        server_ids.append(sid)
    return ",".join(server_ids), {"size": size, "server_ids": server_ids}


def _asg_d(api, pid):
    if not pid:
        return
    for sid in pid.split(","):
        if sid.strip():
            _server_d(api, sid.strip())


def _asg_u(api, pid, old_p, new_p):
    old_ids = [x.strip() for x in (pid or "").split(",") if x.strip()]
    old_size = len(old_ids)
    new_size = int(new_p.get("size", 1))
    prefix = new_p.get("name", "asg")

    if new_size > old_size:
        new_ids = list(old_ids)
        for i in range(old_size, new_size):
            sp = {**new_p, "name": f"{prefix}-{i}"}
            sid, _ = _server_c(api, sp)
            new_ids.append(sid)
        return ",".join(new_ids), {"size": new_size, "server_ids": new_ids}
    elif new_size < old_size:
        keep_ids = old_ids[:new_size]
        drop_ids = old_ids[new_size:]
        for sid in drop_ids:
            _server_d(api, sid)
        return ",".join(keep_ids), {"size": new_size, "server_ids": keep_ids}
    return pid, {"size": new_size, "server_ids": old_ids}


def _nested_stack_c(api, props, depth=1):
    if depth > 3:
        raise ResourceFailure("nested stack depth limit exceeded (max 3)")
    tpl_raw = props.get("template")
    if not tpl_raw:
        raise ResourceFailure("OS::Heat::Stack requires 'template' property")
    t = load(tpl_raw)
    p = merge_params(t, props.get("parameters", {}))
    stack_name = props.get("name", "nested_stack")

    child_id = str(uuid.uuid4())
    db = getattr(api, "db", None)
    parent_id = getattr(api, "parent_id", None)
    project_id = getattr(api, "project_id", "admin")

    child_rec = lambda *a, **k: None
    if db:
        from .stacks import Stack, _recorder
        s = Stack(
            id=child_id,
            parent_id=parent_id,
            project_id=project_id,
            name=stack_name,
            status="CREATE_IN_PROGRESS",
            template=dumps(t),
            parameters=dumps(p),
            disable_rollback=False,
        )
        db.add(s)
        db.commit()
        child_rec = _recorder(db, child_id)

    orig_parent = getattr(api, "parent_id", None)
    api.parent_id = child_id

    try:
        err, child_state = create(t, p, stack_name, api, child_rec, rollback=True, depth=depth)
    finally:
        api.parent_id = orig_parent

    if err:
        if db:
            from .stacks import Stack
            s = db.get(Stack, child_id)
            if s:
                s.status = "CREATE_FAILED"
                s.status_reason = err
                db.commit()
        raise ResourceFailure(f"nested stack '{stack_name}' failed: {err}")

    outs = outputs(t, p, child_state)
    if db:
        from .stacks import Stack
        s = db.get(Stack, child_id)
        if s:
            s.status = "CREATE_COMPLETE"
            s.outputs = dumps(outs)
            db.commit()

    if getattr(api, "_nested_stacks", None) is None:
        api._nested_stacks = {}
    api._nested_stacks[child_id] = (t, child_state)

    return child_id, {"outputs": outs, **outs}


def _nested_stack_d(api, pid):
    db = getattr(api, "db", None)
    if db:
        from .stacks import Stack, StackResource, StackEvent, _recorder
        child_stack = db.get(Stack, pid)
        if child_stack:
            t = json.loads(child_stack.template)
            rows = {r.name: r for r in db.scalars(select(StackResource).where(StackResource.stack_id == pid))}
            items = [(n, rows[n].type, rows[n].physical_id) for n in reversed(order(t)) if n in rows and rows[n].physical_id]
            child_rec = _recorder(db, pid)
            destroy(items, api, child_rec)
            for r in db.scalars(select(StackResource).where(StackResource.stack_id == pid)):
                db.delete(r)
            for e in db.scalars(select(StackEvent).where(StackEvent.stack_id == pid)):
                db.delete(e)
            db.delete(child_stack)
            db.commit()
            return

    nested_stacks = getattr(api, "_nested_stacks", {})
    if pid in nested_stacks:
        t, child_state = nested_stacks.pop(pid)
        items = [(n, t["resources"][n]["type"], child_state[n]["id"]) for n in reversed(order(t)) if n in child_state and "id" in child_state[n]]
        destroy(items, api, lambda *a, **k: None)


def _nested_stack_u(api, pid, old_p, new_p, depth=1):
    if depth > 3:
        raise ResourceFailure("nested stack depth limit exceeded (max 3)")
    new_raw = new_p.get("template")
    if not new_raw:
        raise ResourceFailure("OS::Heat::Stack requires 'template' property")
    new_t = load(new_raw)
    new_p_merged = merge_params(new_t, new_p.get("parameters", {}))

    old_raw = old_p.get("template")
    old_t = load(old_raw) if old_raw else new_t
    old_p_merged = merge_params(old_t, old_p.get("parameters", {}))

    db = getattr(api, "db", None)
    child_rec = lambda *a, **k: None
    child_state = {}

    if db:
        from .stacks import Stack, _recorder, _state
        child_rec = _recorder(db, pid)
        child_state = _state(db, pid)
    else:
        nested_stacks = getattr(api, "_nested_stacks", {})
        if pid in nested_stacks:
            old_t, child_state = nested_stacks[pid]

    stack_name = new_p.get("name", "nested_stack")
    orig_parent = getattr(api, "parent_id", None)
    api.parent_id = pid

    try:
        err, updated_state = update(old_t, old_p_merged, new_t, new_p_merged, stack_name, api, child_rec, state=child_state, depth=depth)
    finally:
        api.parent_id = orig_parent

    if err:
        raise ResourceFailure(f"nested stack update failed: {err}")

    outs = outputs(new_t, new_p_merged, updated_state)
    if db:
        from .stacks import Stack
        s = db.get(Stack, pid)
        if s:
            s.template = dumps(new_t)
            s.parameters = dumps(new_p_merged)
            s.outputs = dumps(outs)
            db.commit()

    if getattr(api, "_nested_stacks", None) is None:
        api._nested_stacks = {}
    api._nested_stacks[pid] = (new_t, updated_state)

    return pid, {"outputs": outs, **outs}


TYPES = {
    "OS::Neutron::Net": (_net_c, _del("net", "/networks"), None),
    "OS::Neutron::Subnet": (_subnet_c, _del("net", "/subnets"), None),
    "OS::Neutron::Router": (_router_c, _router_d, _router_u),
    "OS::Neutron::RouterInterface": (_iface_c, _iface_d, None),
    "OS::Neutron::FloatingIP": (_fip_c, _del("net", "/floating-ips"), None),
    "OS::Neutron::FloatingIPAssociation": (_fipassoc_c, _fipassoc_d, _fipassoc_u),
    "OS::Neutron::SecurityGroup": (_sg_c, _del("net", "/security-groups"), _sg_u),
    "OS::Cinder::Volume": (_vol_c, _vol_d, _vol_u),
    "OS::Nova::Server": (_server_c, _server_d, _server_u),
    "Cloudx::AutoScalingGroup": (_asg_c, _asg_d, _asg_u),
    "OS::Heat::Stack": (_nested_stack_c, _nested_stack_d, _nested_stack_u),
    "Cloudx::Stack": (_nested_stack_c, _nested_stack_d, _nested_stack_u),
}


# ---- driver ----------------------------------------------------------------------------------------------------

def destroy(items, api, record) -> list[str]:
    """Delete (name, type, physical_id) items in the order given. Keeps going on failure; returns the failures."""
    failed = []
    for name, rtype, pid in items:
        record(name, rtype, "DELETE_IN_PROGRESS")
        try:
            TYPES[rtype][1](api, pid)
            record(name, rtype, "DELETE_COMPLETE")
        except Exception as exc:  # best-effort: report each failure, keep tearing down the rest
            record(name, rtype, "DELETE_FAILED", str(exc)[:250])
            failed.append(f"{name}: {exc}")
    return failed


def create(template, params, stack_name, api, record, rollback=True, state=None, depth=1):
    """Create resources in dependency order. Returns (error | None, state). On error, rolls back what was built.

    `state` pre-seeds resources that already exist (stack update): those are skipped and never rolled back.
    """
    state, done = dict(state or {}), []
    for name in order(template):
        if name in state:
            continue
        res = template["resources"][name]
        rtype = res["type"]
        try:
            props = resolve(res.get("properties", {}), params, state)
            props.setdefault("name", f"{stack_name}-{name}")
            record(name, rtype, "CREATE_IN_PROGRESS", props=props)
            if rtype in ("OS::Heat::Stack", "Cloudx::Stack"):
                pid, attrs = _nested_stack_c(api, props, depth=depth + 1)
            else:
                pid, attrs = TYPES[rtype][0](api, props)
        except Exception as exc:  # best-effort: any handler failure fails the stack, never the worker
            err = f"{name}: {exc}"
            record(name, rtype, "CREATE_FAILED", str(exc)[:250])
            if rollback:
                destroy(reversed(done), api, record)
            return err, state
        state[name] = {"id": pid, **attrs}
        done.append((name, rtype, pid))
        record(name, rtype, "CREATE_COMPLETE", pid=pid, attrs=state[name])
    return None, state


def dumps(v) -> str:
    return json.dumps(v, default=str)


def _can_update_inplace(rtype: str, old_p: dict, new_p: dict) -> bool:
    if rtype == "OS::Nova::Server":
        diff_keys = {k for k in set(old_p) | set(new_p) if old_p.get(k) != new_p.get(k)}
        return diff_keys.issubset({"user_data", "flavor"})
    elif rtype == "OS::Cinder::Volume":
        diff_keys = {k for k in set(old_p) | set(new_p) if old_p.get(k) != new_p.get(k)}
        if diff_keys == {"size"}:
            return new_p.get("size", 0) > old_p.get("size", 0)
        return False
    elif rtype == "OS::Neutron::Router":
        diff_keys = {k for k in set(old_p) | set(new_p) if old_p.get(k) != new_p.get(k)}
        return diff_keys.issubset({"routes"})
    elif rtype == "OS::Neutron::SecurityGroup":
        diff_keys = {k for k in set(old_p) | set(new_p) if old_p.get(k) != new_p.get(k)}
        return diff_keys.issubset({"rules"})
    elif rtype == "OS::Neutron::FloatingIPAssociation":
        diff_keys = {k for k in set(old_p) | set(new_p) if old_p.get(k) != new_p.get(k)}
        return diff_keys.issubset({"floatingip_id", "server_id", "port_id"})
    elif rtype == "Cloudx::AutoScalingGroup":
        diff_keys = {k for k in set(old_p) | set(new_p) if old_p.get(k) != new_p.get(k)}
        return diff_keys.issubset({"size", "name", "image", "flavor", "network"})
    elif rtype in ("OS::Heat::Stack", "Cloudx::Stack"):
        return True
    return False


def update(old_template: dict, old_params: dict, new_template: dict, new_params: dict, stack_name: str, api, record, state=None, depth=1):
    """Update stack resources. In-place update where supported, replace (delete + create) where required.

    In-place vs Replace Matrix:
    - OS::Nova::Server:
      - In-place: user_data (PUT /servers/{id}/user-data), flavor (POST /servers/{id}/resize)
      - Replace: name, image, networks, key_name, availability_zone, volume_size, security_groups
    - OS::Cinder::Volume:
      - In-place: size (if size increases, POST /volumes/{id}/extend)
      - Replace: size (if size decreases), name, image
    - OS::Neutron::Router:
      - In-place: routes (PUT /routers/{id})
      - Replace: name, external_gateway_info
    - OS::Neutron::SecurityGroup:
      - In-place: rules (POST /security-groups/{id}/rules & DELETE /security-group-rules/{id})
      - Replace: name, description
    - OS::Neutron::FloatingIPAssociation:
      - In-place: floatingip_id, server_id, port_id (POST /floating-ips/{id}/associate)
    - Cloudx::AutoScalingGroup:
      - In-place: size, name, image, flavor, network (scales servers up/down)
    - OS::Heat::Stack / Cloudx::Stack:
      - In-place: template, parameters (recursive child stack update)
    - OS::Neutron::Net, OS::Neutron::Subnet, OS::Neutron::FloatingIP, OS::Neutron::RouterInterface:
      - Replace (target APIs do not provide update routes)
    """
    state = dict(state or {})
    before = {r["name"]: r for r in preview(old_template, old_params)}
    after = {r["name"]: r for r in preview(new_template, new_params)}

    old_names = set(before)
    new_names = set(after)

    gone = [n for n in reversed(order(old_template)) if n not in new_names]
    common = [n for n in order(new_template) if n in old_names]

    inplace_updates = []
    replaces = []

    for n in common:
        if before[n] != after[n]:
            rtype = new_template["resources"][n]["type"]
            old_p = before[n]["properties"]
            new_p = after[n]["properties"]
            if _can_update_inplace(rtype, old_p, new_p):
                inplace_updates.append((n, rtype, old_p, new_p))
            else:
                replaces.append(n)

    to_delete = [n for n in reversed(order(old_template)) if n in gone or n in replaces]
    for n in to_delete:
        if n in state and state[n].get("id"):
            pid = state[n]["id"]
            rtype = old_template["resources"][n]["type"]
            record(n, rtype, "DELETE_IN_PROGRESS")
            try:
                TYPES[rtype][1](api, pid)
                record(n, rtype, "DELETE_COMPLETE")
            except Exception as exc:
                record(n, rtype, "DELETE_FAILED", str(exc)[:250])
                return f"{n}: {exc}", state
            del state[n]

    for n, rtype, old_p, new_p in inplace_updates:
        if n in state and state[n].get("id"):
            pid = state[n]["id"]
            record(n, rtype, "UPDATE_IN_PROGRESS", props=new_p)
            try:
                if rtype in ("OS::Heat::Stack", "Cloudx::Stack"):
                    res = _nested_stack_u(api, pid, old_p, new_p, depth=depth + 1)
                else:
                    handler = TYPES[rtype][2]
                    res = handler(api, pid, old_p, new_p) if (len(TYPES[rtype]) > 2 and TYPES[rtype][2]) else (pid, {})
                new_pid, attrs = res if isinstance(res, tuple) else (pid, res or {})
                if new_pid:
                    state[n]["id"] = new_pid
                if attrs:
                    state[n].update(attrs)
                record(n, rtype, "UPDATE_COMPLETE", pid=state[n]["id"], props=new_p, attrs=state[n])
            except Exception as exc:
                record(n, rtype, "UPDATE_FAILED", str(exc)[:250])
                return f"{n}: {exc}", state

    for n in order(new_template):
        if n in state:
            continue
        res = new_template["resources"][n]
        rtype = res["type"]
        try:
            props = resolve(res.get("properties", {}), new_params, state)
            props.setdefault("name", f"{stack_name}-{n}")
            record(n, rtype, "CREATE_IN_PROGRESS", props=props)
            if rtype in ("OS::Heat::Stack", "Cloudx::Stack"):
                pid, attrs = _nested_stack_c(api, props, depth=depth + 1)
            else:
                pid, attrs = TYPES[rtype][0](api, props)
        except Exception as exc:
            err = f"{n}: {exc}"
            record(n, rtype, "CREATE_FAILED", str(exc)[:250])
            return err, state
        state[n] = {"id": pid, **attrs}
        record(n, rtype, "CREATE_COMPLETE", pid=pid, attrs=state[n])

    return None, state
