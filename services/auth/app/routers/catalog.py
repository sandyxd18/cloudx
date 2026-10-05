from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..db import DB
from ..deps import AdminUser, CurrentUser
from ..models import ServiceCatalogEntry, ServiceEndpoint
from ..schemas import EndpointIn, ServiceIn
from ..tokens import _build_catalog

router = APIRouter()


def _svc_to_dict(svc: ServiceCatalogEntry) -> dict:
    return {
        "id": svc.id, "name": svc.name, "type": svc.type,
        "description": svc.description, "enabled": svc.enabled,
        "created_at": svc.created_at.isoformat(),
    }


def _ep_to_dict(ep: ServiceEndpoint) -> dict:
    return {
        "id": ep.id, "service_id": ep.service_id,
        "interface": ep.interface, "region": ep.region,
        "url": ep.url, "enabled": ep.enabled,
    }


@router.post("/v3/services", status_code=201)
def create_service(payload: ServiceIn, _: AdminUser, db: DB):
    if db.scalar(select(ServiceCatalogEntry).where(ServiceCatalogEntry.name == payload.name)):
        raise HTTPException(status_code=409, detail="service already exists")
    svc = ServiceCatalogEntry(**payload.model_dump())
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return _svc_to_dict(svc)


@router.get("/v3/services")
def list_services(_: CurrentUser, db: DB):
    svcs = db.scalars(select(ServiceCatalogEntry).order_by(ServiceCatalogEntry.name)).all()
    return {"items": [_svc_to_dict(s) for s in svcs]}


@router.delete("/v3/services/{service_id}", status_code=204)
def delete_service(service_id: str, _: AdminUser, db: DB):
    svc = db.get(ServiceCatalogEntry, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="service not found")
    db.delete(svc)
    db.commit()


@router.post("/v3/endpoints", status_code=201)
def create_endpoint(payload: EndpointIn, _: AdminUser, db: DB):
    svc = db.get(ServiceCatalogEntry, payload.service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="service not found")
    ep = ServiceEndpoint(**payload.model_dump())
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return _ep_to_dict(ep)


@router.get("/v3/endpoints")
def list_endpoints(_: CurrentUser, db: DB, service_id: str | None = None):
    q = select(ServiceEndpoint).order_by(ServiceEndpoint.created_at)
    if service_id:
        q = q.where(ServiceEndpoint.service_id == service_id)
    return {"items": [_ep_to_dict(ep) for ep in db.scalars(q).all()]}


@router.delete("/v3/endpoints/{endpoint_id}", status_code=204)
def delete_endpoint(endpoint_id: str, _: AdminUser, db: DB):
    ep = db.get(ServiceEndpoint, endpoint_id)
    if not ep:
        raise HTTPException(status_code=404, detail="endpoint not found")
    db.delete(ep)
    db.commit()


@router.get("/v3/auth/catalog")
def get_catalog(current: CurrentUser, db: DB):
    """Return the full service catalog for the authenticated user."""
    return {"catalog": _build_catalog(db)}
