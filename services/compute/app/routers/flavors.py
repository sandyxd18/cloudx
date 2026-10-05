from fastapi import APIRouter, HTTPException
from shared.auth_middleware import AdminUser, CurrentUser
from sqlalchemy import select

from ..db import DB
from ..lookups import get_flavor, require_flavor
from ..models import Flavor
from ..schemas import FlavorIn
from ..serializers import obj_to_dict

router = APIRouter()


@router.post("/flavors", status_code=201)
def create_flavor(payload: FlavorIn, _: CurrentUser, db: DB):
    if get_flavor(db, payload.name):
        raise HTTPException(status_code=409, detail="flavor already exists")
    flavor = Flavor(**payload.model_dump())
    db.add(flavor)
    db.commit()
    db.refresh(flavor)
    return obj_to_dict(flavor)


@router.get("/flavors")
def list_flavors(_: CurrentUser, db: DB):
    return {"items": [obj_to_dict(f) for f in db.scalars(select(Flavor).order_by(Flavor.created_at)).all()]}


@router.get("/flavors/{ref}")
def show_flavor(ref: str, _: CurrentUser, db: DB):
    return obj_to_dict(require_flavor(db, ref))


@router.delete("/flavors/{ref}")
def delete_flavor(ref: str, _: AdminUser, db: DB):
    flavor = require_flavor(db, ref)
    db.delete(flavor)
    db.commit()
    return {"status": "deleted", "id": flavor.id, "name": flavor.name}
