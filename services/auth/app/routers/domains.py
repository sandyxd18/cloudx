from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..db import DB
from ..deps import AdminUser, DomainAdminUser, scope_domain
from ..models import Domain, Project, User
from ..schemas import DomainIn, DomainUpdate

router = APIRouter()


def _domain_to_dict(d: Domain) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "enabled": d.enabled,
        "created_at": d.created_at.isoformat(),
    }


@router.post("/v3/domains", status_code=201)
def create_domain(payload: DomainIn, _: AdminUser, db: DB):
    """Create a new domain (admin only)."""
    if db.scalar(select(Domain).where(Domain.name == payload.name)):
        raise HTTPException(status_code=409, detail="domain already exists")
    domain = Domain(**payload.model_dump())
    db.add(domain)
    db.commit()
    db.refresh(domain)
    return _domain_to_dict(domain)


@router.get("/v3/domains")
def list_domains(current: DomainAdminUser, db: DB):
    """List all domains (a domain admin sees only its own)."""
    q = select(Domain).order_by(Domain.name)
    if scope_domain(current) is not None:
        q = q.where(Domain.id == scope_domain(current))
    domains = db.scalars(q).all()
    return {"domains": [_domain_to_dict(d) for d in domains]}


@router.get("/v3/domains/{domain_id}")
def show_domain(domain_id: str, current: DomainAdminUser, db: DB):
    """Get a specific domain by ID or name."""
    domain = db.get(Domain, domain_id)
    if not domain:
        domain = db.scalar(select(Domain).where(Domain.name == domain_id))
    if not domain or scope_domain(current) not in (None, domain.id):
        raise HTTPException(status_code=404, detail="domain not found")
    return _domain_to_dict(domain)


@router.patch("/v3/domains/{domain_id}")
def update_domain(domain_id: str, payload: DomainUpdate, _: AdminUser, db: DB):
    """Update domain properties (admin only)."""
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=404, detail="domain not found")
    if domain.id == "default" and payload.enabled is False:
        raise HTTPException(status_code=403, detail="cannot disable the default domain")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(domain, field, value)
    db.commit()
    db.refresh(domain)
    return _domain_to_dict(domain)


@router.delete("/v3/domains/{domain_id}", status_code=204)
def delete_domain(domain_id: str, _: AdminUser, db: DB):
    """Delete a domain (admin only). Cannot delete the default domain."""
    if domain_id == "default":
        raise HTTPException(status_code=403, detail="cannot delete the default domain")
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=404, detail="domain not found")
    if db.scalar(select(Project.id).where(Project.domain_id == domain.id).limit(1)) or \
            db.scalar(select(User.id).where(User.domain_id == domain.id).limit(1)):
        raise HTTPException(status_code=409, detail="domain still has projects or users")
    db.delete(domain)
    db.commit()
