import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from shared import policy
from shared.auth_middleware import CurrentUser
from sqlalchemy import select

from ..db import DB
from ..lookups import get_flavor, require_flavor
from ..models import Flavor, now
from ..schemas import FlavorIn
from ..serializers import obj_to_dict

router = APIRouter()


def _flavor(f: Flavor) -> dict:
    d = obj_to_dict(f)
    d["extra_specs"] = json.loads(f.extra_specs or "{}")
    return d


@router.post("/flavors", status_code=201)
def create_flavor(payload: FlavorIn, _: Annotated[dict, Depends(policy.require("compute:flavor:create"))], db: DB):
    if get_flavor(db, payload.name):
        raise HTTPException(status_code=409, detail="flavor already exists")
    flavor = Flavor(**{**payload.model_dump(), "extra_specs": json.dumps(payload.extra_specs)})
    db.add(flavor)
    db.commit()
    db.refresh(flavor)
    return _flavor(flavor)


@router.get("/flavors")
def list_flavors(current: CurrentUser, db: DB):
    query = select(Flavor).order_by(Flavor.created_at)
    if current.get("role") != "admin":
        query = query.where(Flavor.is_public.is_(True))
    return {"items": [_flavor(f) for f in db.scalars(query).all()]}


@router.get("/flavors/{ref}")
def show_flavor(ref: str, current: CurrentUser, db: DB):
    return _flavor(require_flavor(db, ref, current))


@router.get("/flavors/{ref}/os-extra_specs")
def get_extra_specs(ref: str, current: CurrentUser, db: DB):
    return {"extra_specs": _flavor(require_flavor(db, ref, current))["extra_specs"]}


@router.post("/flavors/{ref}/os-extra_specs")
def set_extra_specs(ref: str, payload: dict, current: Annotated[dict, Depends(policy.require("compute:flavor-extra-spec:set"))], db: DB):
    flavor = require_flavor(db, ref, current)
    specs = payload.get("extra_specs")
    if not isinstance(specs, dict):
        raise HTTPException(status_code=400, detail="extra_specs object is required")
    merged = {**json.loads(flavor.extra_specs or "{}"), **{str(k): str(v) for k, v in specs.items()}}
    flavor.extra_specs, flavor.updated_at = json.dumps(merged), now()
    db.commit()
    return {"extra_specs": merged}


@router.delete("/flavors/{ref}/os-extra_specs/{key}", status_code=204)
def delete_extra_spec(ref: str, key: str, current: Annotated[dict, Depends(policy.require("compute:flavor-extra-spec:delete"))], db: DB):
    flavor = require_flavor(db, ref, current)
    specs = json.loads(flavor.extra_specs or "{}")
    if key not in specs:
        raise HTTPException(status_code=404, detail="extra spec not found")
    del specs[key]
    flavor.extra_specs, flavor.updated_at = json.dumps(specs), now()
    db.commit()

@router.delete("/flavors/{ref}")
def delete_flavor(ref: str, current: Annotated[dict, Depends(policy.require("compute:flavor:delete"))], db: DB):
    flavor = require_flavor(db, ref, current)
    db.delete(flavor)
    db.commit()
    return {"status": "deleted", "id": flavor.id, "name": flavor.name}
