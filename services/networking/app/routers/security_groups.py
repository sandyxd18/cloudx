import ipaddress

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from .. import ovn_cli
from ..access import own, require_owned, scope
from ..db import DB
from ..quota import check_quota
from ..ipam import parse_id_list
from ..models import Port, SecurityGroup, SecurityGroupRule, now
from ..ovn_cli import (
    acl_match,
    acl_priority,
    apply_security_group_acls,
    ensure_security_group_port_group,
    port_group_name,
)
from ..schemas import SecurityGroupIn, SecurityGroupRuleIn
from ..serializers import get_by_ref, obj_to_dict, require_by_ref, sg_to_os_dict

router = APIRouter()


@router.post("/security-groups", status_code=201)
def create_security_group(payload: SecurityGroupIn, current: CurrentUser, db: DB):
    if get_by_ref(db, SecurityGroup, payload.name):
        raise HTTPException(status_code=409, detail="security-group already exists")
    check_quota(db, current.get("project_id"), SecurityGroup, "security_groups")
    group = SecurityGroup(name=payload.name, description=payload.description, project_id=current.get("project_id"))
    db.add(group)
    db.commit()
    db.refresh(group)
    apply_security_group_acls(db, group)
    return obj_to_dict(group)

PRESET_SECURITY_GROUPS = {
    "ssh": {
        "name": "SSH Only",
        "description": "Allow inbound SSH (port 22) traffic from anywhere",
        "rules": [
            {"direction": "ingress", "protocol": "tcp", "port_min": 22, "port_max": 22, "remote_ip_prefix": "0.0.0.0/0"}
        ],
    },
    "web": {
        "name": "Web Server (HTTP/HTTPS)",
        "description": "Allow inbound HTTP (80) and HTTPS (443) traffic",
        "rules": [
            {"direction": "ingress", "protocol": "tcp", "port_min": 80, "port_max": 80, "remote_ip_prefix": "0.0.0.0/0"},
            {"direction": "ingress", "protocol": "tcp", "port_min": 443, "port_max": 443, "remote_ip_prefix": "0.0.0.0/0"},
        ],
    },
    "db": {
        "name": "Database Server (MySQL/PostgreSQL)",
        "description": "Allow inbound MySQL (3306) and PostgreSQL (5432) traffic",
        "rules": [
            {"direction": "ingress", "protocol": "tcp", "port_min": 3306, "port_max": 3306, "remote_ip_prefix": "0.0.0.0/0"},
            {"direction": "ingress", "protocol": "tcp", "port_min": 5432, "port_max": 5432, "remote_ip_prefix": "0.0.0.0/0"},
        ],
    },
    "ping": {
        "name": "Ping / ICMP",
        "description": "Allow inbound ICMP ping requests",
        "rules": [
            {"direction": "ingress", "protocol": "icmp", "port_min": None, "port_max": None, "remote_ip_prefix": "0.0.0.0/0"},
        ],
    },
    "all-traffic": {
        "name": "All Traffic",
        "description": "Allow all inbound TCP, UDP, and ICMP traffic",
        "rules": [
            {"direction": "ingress", "protocol": "any", "port_min": None, "port_max": None, "remote_ip_prefix": "0.0.0.0/0"},
        ],
    },
}

@router.get("/security-groups/presets")
def get_security_group_presets(current: CurrentUser):
    return {"presets": PRESET_SECURITY_GROUPS}

@router.post("/security-groups/{ref}/apply-preset")
def apply_security_group_preset(ref: str, payload: dict, current: CurrentUser, db: DB):
    preset_key = payload.get("preset")
    if not preset_key or preset_key not in PRESET_SECURITY_GROUPS:
        raise HTTPException(status_code=400, detail=f"Invalid preset key '{preset_key}'. Allowed: {list(PRESET_SECURITY_GROUPS.keys())}")

    group = require_owned(current, db, SecurityGroup, ref, "security-group")
    preset = PRESET_SECURITY_GROUPS[preset_key]
    created_rules = []

    for rule_def in preset["rules"]:
        rule = SecurityGroupRule(
            security_group_id=group.id,
            direction=rule_def.get("direction", "ingress"),
            ethertype="IPv4",
            protocol=rule_def.get("protocol", "tcp"),
            port_min=str(rule_def["port_min"]) if rule_def.get("port_min") is not None else None,
            port_max=str(rule_def.get("port_max", rule_def.get("port_min"))) if rule_def.get("port_min") is not None else None,
            remote_ip_prefix=rule_def.get("remote_ip_prefix", "0.0.0.0/0"),
            action="allow",
        )
        db.add(rule)
        created_rules.append(rule)

    group.updated_at = now()
    db.commit()
    apply_security_group_acls(db, group)
    return {"status": "applied", "preset": preset_key, "rules_added": len(created_rules)}

@router.get("/security-groups")
def list_security_groups(current: CurrentUser, db: DB):
    return {"items": [sg_to_os_dict(db, group, current, include_rules=False) for group in db.scalars(scope(select(SecurityGroup), SecurityGroup, current).order_by(SecurityGroup.created_at)).all()]}

@router.get("/security-groups/{ref}")
def show_security_group(ref: str, current: CurrentUser, db: DB):
    group = require_owned(current, db, SecurityGroup, ref, "security-group")
    return sg_to_os_dict(db, group, current, include_rules=True)

@router.delete("/security-groups/{ref}")
def delete_security_group(ref: str, current: CurrentUser, db: DB):
    group = require_owned(current, db, SecurityGroup, ref, "security-group")
    in_use = [p.name for p in db.scalars(select(Port).where(Port.security_group_ids.is_not(None))).all() if group.id in parse_id_list(p.security_group_ids)]
    if in_use:
        raise HTTPException(status_code=409, detail=f"security-group is still attached to ports: {', '.join(in_use)}")
    for rule in db.scalars(select(SecurityGroupRule).where(SecurityGroupRule.security_group_id == group.id)).all():
        db.delete(rule)
    ovn_cli.ovn("--if-exists", "pg-del", port_group_name(group.id), check=False)
    db.delete(group)
    db.commit()
    return {"status": "deleted", "id": group.id, "name": group.name}

@router.post("/security-groups/{ref}/rules", status_code=201)
def create_security_group_rule(ref: str, payload: SecurityGroupRuleIn, current: CurrentUser, db: DB):
    group = require_owned(current, db, SecurityGroup, ref, "security-group")
    if payload.direction not in {"ingress", "egress"}:
        raise HTTPException(status_code=400, detail="direction must be ingress or egress")
    if payload.protocol not in {"tcp", "udp", "icmp", "any"}:
        raise HTTPException(status_code=400, detail="protocol must be tcp, udp, icmp, or any")
    if payload.ethertype not in {"IPv4", "IPv6"}:
        raise HTTPException(status_code=400, detail="ethertype must be IPv4 or IPv6")
    if payload.remote_ip_prefix:
        try:
            version = ipaddress.ip_network(payload.remote_ip_prefix, strict=False).version
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if version != (6 if payload.ethertype == "IPv6" else 4):
            raise HTTPException(status_code=400, detail="remote_ip_prefix does not match ethertype")
    rule = SecurityGroupRule(
        security_group_id=group.id,
        direction=payload.direction,
        ethertype=payload.ethertype,
        protocol=payload.protocol,
        port_min=str(payload.port_min) if payload.port_min is not None else None,
        port_max=str(payload.port_max or payload.port_min) if payload.port_min is not None else None,
        remote_ip_prefix=payload.remote_ip_prefix,
        remote_group_id=payload.remote_group_id,
        action=payload.action,
    )
    db.add(rule)
    group.updated_at = now()
    db.commit()
    db.refresh(rule)
    apply_security_group_acls(db, group)
    return obj_to_dict(rule)

@router.get("/security-groups/{ref}/rules")
def list_security_group_rules(ref: str, current: CurrentUser, db: DB):
    group = require_owned(current, db, SecurityGroup, ref, "security-group")
    return {"items": [obj_to_dict(rule) for rule in db.scalars(select(SecurityGroupRule).where(SecurityGroupRule.security_group_id == group.id).order_by(SecurityGroupRule.created_at)).all()]}

@router.delete("/security-group-rules/{rule_id}")
def delete_security_group_rule(rule_id: str, current: CurrentUser, db: DB):
    rule = require_by_ref(db, SecurityGroupRule, rule_id, "security-group-rule")
    group = db.get(SecurityGroup, rule.security_group_id)
    if group:
        own(current, group, "security-group-rule")
    db.delete(rule)
    if group:
        group.updated_at = now()
    db.commit()
    if group:
        apply_security_group_acls(db, group)
    return {"status": "deleted", "id": rule_id}

@router.get("/security-groups/{ref}/ovn")
def security_group_ovn_show(ref: str, current: CurrentUser, db: DB):
    group = require_owned(current, db, SecurityGroup, ref, "security-group")
    pg = ensure_security_group_port_group(group)
    members = []
    for port in db.scalars(select(Port).where(Port.security_group_ids.is_not(None)).order_by(Port.created_at)).all():
        if group.id in parse_id_list(port.security_group_ids) and port.port_security_enabled:
            members.append(port.logical_port_name or port.name)
    rules = db.scalars(select(SecurityGroupRule).where(SecurityGroupRule.security_group_id == group.id).order_by(SecurityGroupRule.created_at)).all()
    translated = [
        {
            "rule_id": rule.id,
            "direction": "to-lport" if rule.direction == "ingress" else "from-lport",
            "priority": acl_priority(rule),
            "match": acl_match(group.id, rule),
            "action": "allow-related" if rule.action == "allow" else rule.action,
        }
        for rule in rules
    ]
    translated.append({"rule_id": "default-drop-ingress", "direction": "to-lport", "priority": "1001", "match": f"outport == @{pg} && ip4", "action": "drop"})
    return {"security_group": obj_to_dict(group), "port_group": pg, "member_ports": members, "translated_rules": translated}

@router.get("/ovn/show")
def ovn_show():
    return {"output": ovn_cli.ovn("show")}
