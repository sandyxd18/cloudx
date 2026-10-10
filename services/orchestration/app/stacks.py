"""Heat-like stacks: /stacks CRUD, preview, resources, events, outputs, suspend/resume.

Create/update/delete answer 202 and run in a background thread of the API process, using the caller's own token.
ponytail: a restart mid-run leaves a stack *_IN_PROGRESS and a token that expires mid-run fails the stack; move to
orchestration-worker + a service token if long stacks matter.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from shared.auth_middleware import CurrentUser

from . import engine as eng
from .main import DB, Base, SessionLocal

router = APIRouter()
now = lambda: datetime.now(timezone.utc)  # noqa: E731


class Stack(Base):
    __tablename__ = "orchestration_stacks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="CREATE_IN_PROGRESS")
    status_reason: Mapped[str | None] = mapped_column(String(255))
    template: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[str] = mapped_column(Text, default="{}")
    outputs: Mapped[str] = mapped_column(Text, default="{}")
    disable_rollback: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class StackResource(Base):
    __tablename__ = "orchestration_stack_resources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    stack_id: Mapped[str] = mapped_column(String(64), ForeignKey("orchestration_stacks.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    physical_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(255))
    properties: Mapped[str] = mapped_column(Text, default="{}")
    attributes: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class StackEvent(Base):
    __tablename__ = "orchestration_stack_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stack_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StackIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    template: dict | str
    parameters: dict = Field(default_factory=dict)
    disable_rollback: bool = False


class StackUpdate(BaseModel):
    template: dict | str
    parameters: dict = Field(default_factory=dict)


def _run(fn):  # tests replace this with an inline call
    threading.Thread(target=fn, daemon=True).start()


def _stack(db, user, ref) -> Stack:
    s = db.scalar(select(Stack).where((Stack.id == ref) | (Stack.name == ref)).order_by(Stack.created_at.desc()))
    if not s or (user.get("role") != "admin" and s.project_id != user.get("project_id")):
        raise HTTPException(status_code=404, detail="Stack not found")
    return s


def _busy(s):
    if s.status.endswith("IN_PROGRESS"):
        raise HTTPException(status_code=409, detail=f"Stack is {s.status}")


def _api(request: Request) -> eng.Api:
    if not request.headers.get("Authorization"):
        raise HTTPException(status_code=401, detail="Authorization header required")
    return eng.Api(request.headers["Authorization"])


def _parse(template, parameters):
    try:
        t = eng.load(template)
        p = eng.merge_params(t, parameters)
        eng.order(t)
        return t, p
    except eng.TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _out(s: Stack) -> dict:
    return {"id": s.id, "stack_name": s.name, "project_id": s.project_id, "stack_status": s.status,
            "stack_status_reason": s.status_reason, "parameters": json.loads(s.parameters),
            "outputs": json.loads(s.outputs), "disable_rollback": s.disable_rollback,
            "creation_time": s.created_at, "updated_time": s.updated_at}


def _recorder(db, stack_id):
    def record(name, rtype, status, reason=None, pid=None, props=None, attrs=None):
        r = db.scalar(select(StackResource).where(StackResource.stack_id == stack_id, StackResource.name == name))
        if status == "DELETE_COMPLETE" and r:
            db.delete(r)
        else:
            r = r or StackResource(stack_id=stack_id, name=name, type=rtype, status=status)
            r.status, r.status_reason = status, reason
            if pid:
                r.physical_id = pid
            if props is not None:
                r.properties = eng.dumps(props)
            if attrs is not None:
                r.attributes = eng.dumps(attrs)
            db.add(r)
        db.add(StackEvent(stack_id=stack_id, resource_name=name, status=status, reason=reason))
        db.commit()
    return record


def _set(db, s, status, reason=None):
    s.status, s.status_reason = status, (reason or "")[:255] or None
    db.add(StackEvent(stack_id=s.id, status=status, reason=s.status_reason))
    db.commit()


def _state(db, stack_id) -> dict:
    rows = db.scalars(select(StackResource).where(StackResource.stack_id == stack_id, StackResource.status.like("%COMPLETE"))).all()
    return {r.name: {**json.loads(r.attributes), "id": r.physical_id} for r in rows if r.physical_id}


def _create_job(stack_id, api):
    def job():
        with SessionLocal() as db:
            s = db.get(Stack, stack_id)
            t, p = json.loads(s.template), json.loads(s.parameters)
            err, state = eng.create(t, p, s.name, api, _recorder(db, s.id), rollback=not s.disable_rollback)
            if err:
                _set(db, s, "CREATE_FAILED" if s.disable_rollback else "ROLLBACK_COMPLETE", err)
            else:
                s.outputs = eng.dumps(eng.outputs(t, p, state))
                _set(db, s, "CREATE_COMPLETE")
    return job


@router.post("/stacks", status_code=201)
def create_stack(user: CurrentUser, data: StackIn, request: Request, db: DB):
    api = _api(request)
    t, p = _parse(data.template, data.parameters)
    pid = user.get("project_id") or "admin"
    if db.scalar(select(Stack).where(Stack.project_id == pid, Stack.name == data.name)):
        raise HTTPException(status_code=409, detail="Stack name already exists in this project")
    s = Stack(project_id=pid, name=data.name, template=eng.dumps(t), parameters=eng.dumps(p), disable_rollback=data.disable_rollback)
    db.add(s)
    db.flush()  # assigns s.id
    db.add(StackEvent(stack_id=s.id, status="CREATE_IN_PROGRESS"))
    db.commit()
    out = {"id": s.id, "stack_name": s.name, "stack_status": s.status}
    _run(_create_job(out["id"], api))
    return out


@router.post("/stacks/preview")
def preview_stack(user: CurrentUser, data: StackIn):
    t, p = _parse(data.template, data.parameters)
    return {"stack_name": data.name, "parameters": p, "resources": eng.preview(t, p)}


@router.get("/stacks")
def list_stacks(user: CurrentUser, db: DB, status: str | None = None):
    q = select(Stack).order_by(Stack.created_at.desc())
    if user.get("role") != "admin":
        q = q.where(Stack.project_id == user.get("project_id"))
    if status:
        q = q.where(Stack.status == status)
    return {"stacks": [_out(s) for s in db.scalars(q)]}


@router.get("/stacks/{ref}")
def show_stack(user: CurrentUser, ref: str, db: DB):
    return {"stack": _out(_stack(db, user, ref))}


@router.get("/stacks/{ref}/template")
def stack_template(user: CurrentUser, ref: str, db: DB):
    return json.loads(_stack(db, user, ref).template)


@router.get("/stacks/{ref}/outputs")
def stack_outputs(user: CurrentUser, ref: str, db: DB):
    return {"outputs": json.loads(_stack(db, user, ref).outputs)}


@router.get("/stacks/{ref}/resources")
def stack_resources(user: CurrentUser, ref: str, db: DB):
    s = _stack(db, user, ref)
    rows = db.scalars(select(StackResource).where(StackResource.stack_id == s.id).order_by(StackResource.updated_at)).all()
    return {"resources": [{"resource_name": r.name, "resource_type": r.type, "physical_resource_id": r.physical_id,
                           "resource_status": r.status, "resource_status_reason": r.status_reason,
                           "updated_time": r.updated_at} for r in rows]}


@router.get("/stacks/{ref}/events")
def stack_events(user: CurrentUser, ref: str, db: DB):
    s = _stack(db, user, ref)
    rows = db.scalars(select(StackEvent).where(StackEvent.stack_id == s.id).order_by(StackEvent.id)).all()
    return {"events": [{"id": e.id, "resource_name": e.resource_name or s.name, "resource_status": e.status,
                        "resource_status_reason": e.reason, "event_time": e.created_at} for e in rows]}


def recover_stuck_stacks(db: Session) -> int:
    stuck = db.scalars(select(Stack).where(Stack.status.like("%_IN_PROGRESS"))).all()
    for s in stuck:
        target = s.status.replace("_IN_PROGRESS", "_FAILED") if s.status.endswith("_IN_PROGRESS") else f"{s.status}_FAILED"
        s.status = target
        s.status_reason = "interrupted by service restart"
        db.add(StackEvent(stack_id=s.id, status=target, reason="interrupted by service restart"))
    if stuck:
        db.commit()
    return len(stuck)


@router.put("/stacks/{ref}", status_code=202)
def update_stack(user: CurrentUser, ref: str, data: StackUpdate, request: Request, db: DB):
    api = _api(request)
    s = _stack(db, user, ref)
    _busy(s)
    if s.status.startswith("ROLLBACK") or s.status == "CREATE_FAILED":
        raise HTTPException(status_code=409, detail=f"Stack is {s.status}; delete it and create it again")
    old, (new, p) = json.loads(s.template), _parse(data.template, data.parameters)
    sid = s.id
    s.status = "UPDATE_IN_PROGRESS"
    db.add(StackEvent(stack_id=sid, status=s.status))
    db.commit()

    def job():
        with SessionLocal() as db2:
            st = db2.get(Stack, sid)
            rec = _recorder(db2, st.id)
            err, state = eng.update(old, json.loads(st.parameters), new, p, st.name, api, rec, state=_state(db2, st.id))
            if err:
                return _set(db2, st, "UPDATE_FAILED", err)
            st.template, st.parameters, st.outputs = eng.dumps(new), eng.dumps(p), eng.dumps(eng.outputs(new, p, state))
            _set(db2, st, "UPDATE_COMPLETE")
    _run(job)
    return {"id": sid, "stack_status": "UPDATE_IN_PROGRESS"}


@router.delete("/stacks/{ref}", status_code=202)
def delete_stack(user: CurrentUser, ref: str, request: Request, db: DB):
    api = _api(request)
    s = _stack(db, user, ref)
    _busy(s)
    sid = s.id
    s.status = "DELETE_IN_PROGRESS"
    db.add(StackEvent(stack_id=sid, status=s.status))
    db.commit()

    def job():
        with SessionLocal() as db2:
            st = db2.get(Stack, sid)
            t = json.loads(st.template)
            rows = {r.name: r for r in db2.scalars(select(StackResource).where(StackResource.stack_id == sid))}
            items = [(n, rows[n].type, rows[n].physical_id) for n in reversed(eng.order(t)) if n in rows and rows[n].physical_id]
            failed = eng.destroy(items, api, _recorder(db2, sid))
            if failed:
                return _set(db2, st, "DELETE_FAILED", "; ".join(failed))
            for r in db2.scalars(select(StackResource).where(StackResource.stack_id == sid)):
                db2.delete(r)
            for e in db2.scalars(select(StackEvent).where(StackEvent.stack_id == sid)):
                db2.delete(e)
            db2.delete(st)
            db2.commit()
    _run(job)
    return {"id": sid, "stack_status": "DELETE_IN_PROGRESS"}


@router.post("/stacks/{ref}/actions", status_code=202)
def stack_action(user: CurrentUser, ref: str, request: Request, db: DB, body: Annotated[dict, Body()]):
    """{"suspend": null} or {"resume": null}: applies to the stack's servers."""
    api = _api(request)
    s = _stack(db, user, ref)
    _busy(s)
    action = next((a for a in ("suspend", "resume") if a in body), None)
    if not action:
        raise HTTPException(status_code=400, detail="action must be 'suspend' or 'resume'")
    ok = ("CREATE_COMPLETE", "UPDATE_COMPLETE", "RESUME_COMPLETE") if action == "suspend" else ("SUSPEND_COMPLETE",)
    if s.status not in ok:
        raise HTTPException(status_code=409, detail=f"cannot {action} a stack in {s.status}")
    servers = db.scalars(select(StackResource).where(StackResource.stack_id == s.id, StackResource.type == "OS::Nova::Server")).all()
    sid = s.id
    ids = [(r.name, r.physical_id) for r in servers]
    _set(db, s, f"{action.upper()}_IN_PROGRESS")

    def job():
        with SessionLocal() as db2:
            st = db2.get(Stack, sid)
            try:
                for _, vm in ids:
                    api.call("compute", "POST", f"/servers/{vm}/{action}", {})
                _set(db2, st, f"{action.upper()}_COMPLETE")
            except Exception as exc:  # best-effort: report the failure on the stack
                _set(db2, st, f"{action.upper()}_FAILED", str(exc))
    _run(job)
    return {"id": sid, "stack_status": f"{action.upper()}_IN_PROGRESS"}
