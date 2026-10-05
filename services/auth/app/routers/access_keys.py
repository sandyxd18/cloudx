from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..db import DB
from ..deps import CurrentUser
from ..models import AccessKey
from ..schemas import AccessKeyIn
from ..serializers import _key_to_dict
from ..tokens import _gen_access_key_id, _gen_secret_key, _hash_password

router = APIRouter()


# ── Access Keys ────────────────────────────────────────────────────────────────
@router.post("/access-keys", status_code=201)
def create_access_key(payload: AccessKeyIn, current: CurrentUser, db: DB):
    """Generate a new CLDX access key for the current user."""
    access_key_id = _gen_access_key_id()
    secret        = _gen_secret_key()
    ak = AccessKey(
        access_key_id=access_key_id,
        secret_key_hash=_hash_password(secret),
        user_id=current["sub"],
        project_id=current["project_id"],
        name=payload.name,
    )
    db.add(ak)
    db.commit()
    db.refresh(ak)
    # Return the raw secret ONCE — it cannot be retrieved again
    return _key_to_dict(ak, secret=secret)


@router.get("/access-keys")
def list_access_keys(current: CurrentUser, db: DB):
    uid = current["sub"]
    if current.get("role") == "admin":
        keys = db.scalars(select(AccessKey)).all()
    else:
        keys = db.scalars(select(AccessKey).where(AccessKey.user_id == uid)).all()
    return {"items": [_key_to_dict(k) for k in keys]}


@router.delete("/access-keys/{key_id}", status_code=204)
def revoke_access_key(key_id: str, current: CurrentUser, db: DB):
    ak = db.get(AccessKey, key_id) or db.scalar(
        select(AccessKey).where(AccessKey.access_key_id == key_id))
    if not ak:
        raise HTTPException(status_code=404, detail="access key not found")
    if current.get("role") != "admin" and ak.user_id != current["sub"]:
        raise HTTPException(status_code=403, detail="forbidden")
    db.delete(ak)
    db.commit()
