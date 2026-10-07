import json

from fastapi import APIRouter, HTTPException
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from ..db import DB
from ..models import IKEPolicy, IPsecPolicy, Router, VPNSiteConnection
from ..schemas import (
    IKEPolicyIn,
    IKEPolicyOut,
    IPsecPolicyIn,
    IPsecPolicyOut,
    VPNSiteConnectionIn,
    VPNSiteConnectionOut,
)
from ..serializers import require_by_ref

router = APIRouter()


def _own_conn(db, current: dict, conn_id: str) -> VPNSiteConnection:
    conn = db.get(VPNSiteConnection, conn_id)
    pid = current.get("project_id", current.get("tenant_id", "default"))
    if not conn or (current.get("role") != "admin" and conn.project_id != pid):
        raise HTTPException(status_code=404, detail="VPN site connection not found")
    return conn


@router.post("/vpn/ikepolicies", response_model=IKEPolicyOut, status_code=201)
def create_ike_policy(payload: IKEPolicyIn, current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    ike = IKEPolicy(
        name=payload.name,
        project_id=project_id,
        auth_algorithm=payload.auth_algorithm,
        encryption_algorithm=payload.encryption_algorithm,
        phase1_negotiation_mode=payload.phase1_negotiation_mode,
        pfs=payload.pfs,
    )
    db.add(ike)
    db.commit()
    db.refresh(ike)
    return ike

@router.get("/vpn/ikepolicies", response_model=list[IKEPolicyOut])
def list_ike_policies(current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    q = select(IKEPolicy)
    if current.get("role") != "admin":
        q = q.where(IKEPolicy.project_id == project_id)
    return db.scalars(q).all()

@router.get("/vpn/ikepolicies/{policy_id}", response_model=IKEPolicyOut)
def get_ike_policy(policy_id: str, db: DB):
    ike = db.get(IKEPolicy, policy_id)
    if not ike:
        raise HTTPException(status_code=404, detail="IKE policy not found")
    return ike

@router.delete("/vpn/ikepolicies/{policy_id}", status_code=204)
def delete_ike_policy(policy_id: str, db: DB):
    ike = db.get(IKEPolicy, policy_id)
    if not ike:
        raise HTTPException(status_code=404, detail="IKE policy not found")
    db.delete(ike)
    db.commit()

@router.post("/vpn/ipsecpolicies", response_model=IPsecPolicyOut, status_code=201)
def create_ipsec_policy(payload: IPsecPolicyIn, current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    ipsec = IPsecPolicy(
        name=payload.name,
        project_id=project_id,
        auth_algorithm=payload.auth_algorithm,
        encryption_algorithm=payload.encryption_algorithm,
        pfs=payload.pfs,
        transform_protocol=payload.transform_protocol,
    )
    db.add(ipsec)
    db.commit()
    db.refresh(ipsec)
    return ipsec

@router.get("/vpn/ipsecpolicies", response_model=list[IPsecPolicyOut])
def list_ipsec_policies(current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    q = select(IPsecPolicy)
    if current.get("role") != "admin":
        q = q.where(IPsecPolicy.project_id == project_id)
    return db.scalars(q).all()

@router.get("/vpn/ipsecpolicies/{policy_id}", response_model=IPsecPolicyOut)
def get_ipsec_policy(policy_id: str, db: DB):
    ipsec = db.get(IPsecPolicy, policy_id)
    if not ipsec:
        raise HTTPException(status_code=404, detail="IPsec policy not found")
    return ipsec

@router.delete("/vpn/ipsecpolicies/{policy_id}", status_code=204)
def delete_ipsec_policy(policy_id: str, db: DB):
    ipsec = db.get(IPsecPolicy, policy_id)
    if not ipsec:
        raise HTTPException(status_code=404, detail="IPsec policy not found")
    db.delete(ipsec)
    db.commit()

@router.post("/vpn/site-connections", response_model=VPNSiteConnectionOut, status_code=201)
def create_vpn_site_connection(payload: VPNSiteConnectionIn, current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    router = require_by_ref(db, Router, payload.router_id, "router")
    ike = db.get(IKEPolicy, payload.ikepolicy_id)
    if not ike:
        raise HTTPException(status_code=404, detail="IKE policy not found")
    ipsec = db.get(IPsecPolicy, payload.ipsecpolicy_id)
    if not ipsec:
        raise HTTPException(status_code=404, detail="IPsec policy not found")

    conn = VPNSiteConnection(
        name=payload.name,
        project_id=project_id,
        router_id=router.id,
        peer_address=payload.peer_address,
        peer_id=payload.peer_id,
        peer_cidrs_json=json.dumps(payload.peer_cidrs),
        psk=payload.psk,
        ikepolicy_id=ike.id,
        ipsecpolicy_id=ipsec.id,
        admin_state_up=payload.admin_state_up,
        status="ACTIVE",
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return {
        "id": conn.id,
        "name": conn.name,
        "project_id": conn.project_id,
        "router_id": conn.router_id,
        "peer_address": conn.peer_address,
        "peer_id": conn.peer_id,
        "peer_cidrs": json.loads(conn.peer_cidrs_json),
        "ikepolicy_id": conn.ikepolicy_id,
        "ipsecpolicy_id": conn.ipsecpolicy_id,
        "status": conn.status,
        "admin_state_up": conn.admin_state_up,
        "created_at": conn.created_at,
    }

@router.get("/vpn/site-connections", response_model=list[VPNSiteConnectionOut])
def list_vpn_site_connections(current: CurrentUser, db: DB):
    project_id = current.get("project_id", current.get("tenant_id", "default"))
    q = select(VPNSiteConnection)
    if current.get("role") != "admin":
        q = q.where(VPNSiteConnection.project_id == project_id)
    conns = db.scalars(q).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "project_id": c.project_id,
            "router_id": c.router_id,
            "peer_address": c.peer_address,
            "peer_id": c.peer_id,
            "peer_cidrs": json.loads(c.peer_cidrs_json),
            "ikepolicy_id": c.ikepolicy_id,
            "ipsecpolicy_id": c.ipsecpolicy_id,
            "status": c.status,
            "admin_state_up": c.admin_state_up,
            "created_at": c.created_at,
        }
        for c in conns
    ]

@router.get("/vpn/site-connections/{conn_id}", response_model=VPNSiteConnectionOut)
def get_vpn_site_connection(conn_id: str, current: CurrentUser, db: DB):
    conn = _own_conn(db, current, conn_id)
    return {
        "id": conn.id,
        "name": conn.name,
        "project_id": conn.project_id,
        "router_id": conn.router_id,
        "peer_address": conn.peer_address,
        "peer_id": conn.peer_id,
        "peer_cidrs": json.loads(conn.peer_cidrs_json),
        "ikepolicy_id": conn.ikepolicy_id,
        "ipsecpolicy_id": conn.ipsecpolicy_id,
        "status": conn.status,
        "admin_state_up": conn.admin_state_up,
        "created_at": conn.created_at,
    }

@router.delete("/vpn/site-connections/{conn_id}", status_code=204)
def delete_vpn_site_connection(conn_id: str, current: CurrentUser, db: DB):
    conn = _own_conn(db, current, conn_id)
    db.delete(conn)
    db.commit()

@router.post("/vpn/site-connections/{conn_id}/config")
def generate_vpn_config(conn_id: str, current: CurrentUser, db: DB):
    conn = _own_conn(db, current, conn_id)
    ike = db.get(IKEPolicy, conn.ikepolicy_id)
    ipsec = db.get(IPsecPolicy, conn.ipsecpolicy_id)
    cidrs = ", ".join(json.loads(conn.peer_cidrs_json))

    ipsec_conf = f"""conn {conn.name}
    authby=secret
    auto=start
    keyexchange=ikev2
    left=%defaultroute
    right={conn.peer_address}
    rightid={conn.peer_id}
    rightsubnets={cidrs}
    ike={ike.encryption_algorithm if ike else 'aes256'}-{ike.auth_algorithm if ike else 'sha256'}-{ike.pfs if ike else 'modp2048'}
    esp={ipsec.encryption_algorithm if ipsec else 'aes256'}-{ipsec.auth_algorithm if ipsec else 'sha256'}
"""
    ipsec_secrets = f": PSK \"{conn.psk}\"\n"
    return {
        "connection_id": conn.id,
        "ipsec_conf": ipsec_conf,
        "ipsec_secrets": ipsec_secrets,
        "status": conn.status,
    }
