
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from shared import policy as policy_rules  # `policy` is a local name in this module
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from ..db import DB
from ..models import QosBandwidthLimitRule, QosPolicy
from ..schemas import QosBwLimitRuleIn, QosPolicyIn
from ..serializers import get_by_ref

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
    if get_by_ref(db, QosPolicy, payload.name, current=current):
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
def show_qos_policy(policy_id: str, current: CurrentUser, db: DB):
    policy = get_by_ref(db, QosPolicy, policy_id, current=current)
    if not policy:
        raise HTTPException(status_code=404, detail="QoS policy not found")
    rules = db.scalars(select(QosBandwidthLimitRule).where(QosBandwidthLimitRule.qos_policy_id == policy.id)).all()
    return _qos_to_dict(policy, rules)

@router.delete("/qos/policies/{policy_id}", status_code=204)
def delete_qos_policy(
    policy_id: str,
    _: Annotated[dict, Depends(policy_rules.require("networking:qos-policy:delete"))],
    db: DB,
):
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
    # A policy only holds rules for now: no port refers to a policy (ports carry qos_max_kbps and qos_burst_kbps), so there
    # is nothing to apply to OVN here.
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
