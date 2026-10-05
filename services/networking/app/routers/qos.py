
from fastapi import APIRouter, HTTPException
from shared.auth_middleware import AdminUser, CurrentUser
from sqlalchemy import select

from .. import ovn_cli
from ..db import DB
from ..models import Port, QosBandwidthLimitRule, QosPolicy
from ..schemas import QosBwLimitRuleIn, QosPolicyIn

router = APIRouter()

def _qos_to_dict(policy: QosPolicy, rules: list) -> dict:
    return {
        "id": policy.id,
        "name": policy.name,
        "description": policy.description,
        "shared": policy.shared,
        "project_id": policy.project_id,
        "rules": [
            {
                "id": r.id,
                "max_kbps": r.max_kbps,
                "max_burst_kbps": r.max_burst_kbps,
                "direction": r.direction,
                "type": "bandwidth_limit",
            }
            for r in rules
        ],
        "created_at": policy.created_at.isoformat(),
    }

@router.post("/qos/policies", status_code=201)
def create_qos_policy(payload: QosPolicyIn, current: CurrentUser, db: DB):
    if db.scalar(select(QosPolicy).where(QosPolicy.name == payload.name)):
        raise HTTPException(status_code=409, detail="QoS policy already exists")
    policy = QosPolicy(**payload.model_dump(), project_id=current.get("project_id"))
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return _qos_to_dict(policy, [])

@router.get("/qos/policies")
def list_qos_policies(current: CurrentUser, db: DB):
    q = select(QosPolicy)
    if current.get("role") != "admin":
        q = q.where(
            (QosPolicy.shared == True) | (QosPolicy.project_id == current.get("project_id"))
        )
    policies = db.scalars(q).all()
    result = []
    for p in policies:
        rules = db.scalars(select(QosBandwidthLimitRule).where(QosBandwidthLimitRule.qos_policy_id == p.id)).all()
        result.append(_qos_to_dict(p, rules))
    return {"policies": result}

@router.get("/qos/policies/{policy_id}")
def show_qos_policy(policy_id: str, _: CurrentUser, db: DB):
    policy = db.get(QosPolicy, policy_id) or db.scalar(select(QosPolicy).where(QosPolicy.name == policy_id))
    if not policy:
        raise HTTPException(status_code=404, detail="QoS policy not found")
    rules = db.scalars(select(QosBandwidthLimitRule).where(QosBandwidthLimitRule.qos_policy_id == policy.id)).all()
    return _qos_to_dict(policy, rules)

@router.delete("/qos/policies/{policy_id}", status_code=204)
def delete_qos_policy(policy_id: str, _: AdminUser, db: DB):
    policy = db.get(QosPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="QoS policy not found")
    db.delete(policy)
    db.commit()

@router.post("/qos/policies/{policy_id}/bandwidth_limit_rules", status_code=201)
def create_bw_limit_rule(policy_id: str, payload: QosBwLimitRuleIn, _: CurrentUser, db: DB):
    policy = db.get(QosPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="QoS policy not found")
    rule = QosBandwidthLimitRule(
        qos_policy_id=policy_id,
        max_kbps=payload.max_kbps,
        max_burst_kbps=payload.max_burst_kbps,
        direction=payload.direction,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    # Apply OVN QoS on affected ports
    _apply_qos_to_policy_ports(policy_id, rule, db)
    return {
        "id": rule.id, "max_kbps": rule.max_kbps,
        "max_burst_kbps": rule.max_burst_kbps, "direction": rule.direction,
    }

@router.delete("/qos/policies/{policy_id}/bandwidth_limit_rules/{rule_id}", status_code=204)
def delete_bw_limit_rule(policy_id: str, rule_id: str, _: CurrentUser, db: DB):
    rule = db.get(QosBandwidthLimitRule, rule_id)
    if not rule or rule.qos_policy_id != policy_id:
        raise HTTPException(status_code=404, detail="rule not found")
    db.delete(rule)
    db.commit()

def _apply_qos_to_policy_ports(policy_id: str, rule: QosBandwidthLimitRule, db) -> None:
    """Apply OVN QoS meter to all ports associated with this policy."""
    ports = db.scalars(select(Port).where(Port.qos_policy_id == policy_id)).all()
    for port in ports:
        if port.logical_switch_name and port.logical_port_name:
            _apply_ovn_qos(port.logical_switch_name, port.logical_port_name, rule)

def _apply_ovn_qos(ls_name: str, lp_name: str, rule: QosBandwidthLimitRule) -> None:
    """Set OVN QoS bandwidth limit on a logical switch port."""
    # Convert kbps to bps for OVN
    rate_bps = rule.max_kbps * 1000
    burst_bps = rule.max_burst_kbps * 1000 if rule.max_burst_kbps else rate_bps
    direction = "from-lport" if rule.direction == "egress" else "to-lport"
    try:
        # Remove existing QoS rule for this port
        ovn_cli.ovn(f'--where direction={direction} --data-bare --no-heading --columns _uuid find QoS '
            f'match="inport == \'{lp_name}\'"')
        # Add new bandwidth rule
        ovn_cli.ovn(
            f'set Logical_Switch {ls_name} '
            f'qos_rules=[{{"direction": "{direction}", '
            f'"match": "inport == \'{lp_name}\'", '
            f'"bandwidth": {{"rate": {rate_bps}, "burst": {burst_bps}}}, '
            f'"priority": 100}}]'
        )
    except Exception as exc:
        import logging
        logging.getLogger("networking-api").warning("OVN QoS apply failed for port %s: %s", lp_name, exc)
