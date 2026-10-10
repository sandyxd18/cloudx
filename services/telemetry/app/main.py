from __future__ import annotations
import os
from shared.db import get_db_url
from shared.config import load_service_config
load_service_config("telemetry-api")

"""
cloudx Telemetry API — Metrics Collection & Prometheus Exporter
================================================================

Service Phase 5.1: mengumpulkan dan mengekspos metrik real-time per VM/tenant.
"""


import logging
import operator
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, Integer, String, ForeignKey, create_engine, delete, select, text, func, literal
from sqlalchemy.dialects.postgresql import array_agg as pg_array_agg, aggregate_order_by
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from shared.auth_middleware import CurrentUser, AdminUser
from shared.metrics import check_metrics_token
from shared.schema import require_schema

from shared.request_id_middleware import RequestIDMiddleware
from shared.cors import cors_origins

log = logging.getLogger("telemetry-api")

DATABASE_URL = get_db_url("cloudx")
METRICS_RETENTION_DAYS = int(os.getenv("METRICS_RETENTION_DAYS", "7"))      # raw samples
ROLLUP_RETENTION_DAYS = int(os.getenv("ROLLUP_RETENTION_DAYS", "365"))       # hourly rollups
ALARM_INTERVAL = int(os.getenv("ALARM_INTERVAL", "30"))                      # seconds between evaluations

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class TelemetryMetric(Base):
    __tablename__ = "telemetry_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    metric_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
        default=lambda: datetime.now(timezone.utc)
    )
    host: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TelemetryMetricType(Base):
    __tablename__ = "telemetry_metric_types"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    default_enabled: Mapped[bool] = mapped_column(default=True)


class TelemetryRollup(Base):
    """Hourly aggregate of raw samples that aged out of METRICS_RETENTION_DAYS. Several rows may share a bucket
    (a late sample, a re-run); readers merge them with sum(avg*samples)/sum(samples)."""
    __tablename__ = "telemetry_rollups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    metric_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    avg_value: Mapped[float] = mapped_column(Float, nullable=False)
    min_value: Mapped[float] = mapped_column(Float, nullable=False)
    max_value: Mapped[float] = mapped_column(Float, nullable=False)
    samples: Mapped[int] = mapped_column(Integer, nullable=False)


class TelemetryAlarm(Base):
    """Threshold alarm: avg(metric) over the last `period` seconds <comparison> threshold -> state `alarm`."""
    __tablename__ = "telemetry_alarms"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(32), nullable=False)
    instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # None = every instance of the project
    comparison: Mapped[str] = mapped_column(String(2), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_periods: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    statistic: Mapped[str] = mapped_column(String(16), nullable=False, default="avg")
    breach_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="insufficient_data")
    state_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=lambda: datetime.now(timezone.utc))

    history: Mapped[list[TelemetryAlarmHistory]] = relationship("TelemetryAlarmHistory", cascade="all, delete-orphan")


class TelemetryAlarmHistory(Base):
    __tablename__ = "telemetry_alarm_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alarm_id: Mapped[str] = mapped_column(String(36), ForeignKey("telemetry_alarms.id", ondelete="CASCADE"), nullable=False, index=True)
    old_state: Mapped[str] = mapped_column(String(24), nullable=False)
    new_state: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


def init_db():
    require_schema(engine, "telemetry")
    defaults = [
        TelemetryMetricType(name="cpu_percent", description="CPU utilization percentage", unit="%"),
        TelemetryMetricType(name="memory_used_mb", description="Memory used in MB", unit="MB"),
        TelemetryMetricType(name="memory_total_mb", description="Total memory in MB", unit="MB"),
        TelemetryMetricType(name="disk_read_bytes", description="Disk read throughput", unit="B/s"),
        TelemetryMetricType(name="disk_write_bytes", description="Disk write throughput", unit="B/s"),
        TelemetryMetricType(name="net_rx_bytes", description="Network received bytes", unit="B/s"),
        TelemetryMetricType(name="net_tx_bytes", description="Network transmitted bytes", unit="B/s"),
    ]
    with SessionLocal() as db:
        existing = set(db.execute(select(TelemetryMetricType.name)).scalars().all())
        for mt in defaults:
            if mt.name not in existing:
                db.add(mt)
        db.commit()
    log.info("Telemetry metric types seeded")


app = FastAPI(
    title="CloudX Telemetry API",
    description="Metrics collection and Prometheus exporter for CloudX",
    version="5.1.0",
)

if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    threading.Thread(target=_alarm_loop, daemon=True, name="alarm-loop").start()
    log.info("Telemetry API started")


# ── Pydantic models ───────────────────────────────────────────────────────────

class MetricIn(BaseModel):
    instance_id: str
    project_id: str | None = None
    metric_type: str
    value: float
    unit: str | None = None
    timestamp: datetime | None = None
    host: str | None = None


class MetricOut(BaseModel):
    id: int
    instance_id: str
    project_id: str | None
    metric_type: str
    value: float
    unit: str | None
    timestamp: datetime
    host: str | None

    class Config:
        from_attributes = True


class MetricTypeOut(BaseModel):
    id: int
    name: str
    description: str | None
    unit: str | None
    default_enabled: bool

    class Config:
        from_attributes = True


def _scope(user: dict, project_id: str | None) -> str | None:
    """Project filter: admin may choose (None = all); others pinned to own project."""
    if user.get("role") == "admin":
        return project_id
    return user.get("project_id") or "\0none"


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.post("/telemetry/metrics", response_model=MetricOut, status_code=status.HTTP_201_CREATED)
def create_metric(user: AdminUser, data: MetricIn, db: DB):
    metric = TelemetryMetric(**data.model_dump())
    db.add(metric)
    db.commit()
    db.refresh(metric)
    return metric


@app.post("/telemetry/metrics/batch", status_code=status.HTTP_201_CREATED)
def create_metrics_batch(user: AdminUser, data: list[MetricIn], db: DB):
    metrics = [TelemetryMetric(**m.model_dump()) for m in data]
    db.add_all(metrics)
    db.commit()
    return {"ingested": len(metrics)}


@app.get("/telemetry/metrics", response_model=list[MetricOut])
def list_metrics(
    user: CurrentUser,
    db: DB,
    instance_id: str | None = None,
    project_id: str | None = None,
    metric_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    q = select(TelemetryMetric)
    if instance_id:
        q = q.where(TelemetryMetric.instance_id == instance_id)
    scope = _scope(user, project_id)
    if scope:
        q = q.where(TelemetryMetric.project_id == scope)
    if metric_type:
        q = q.where(TelemetryMetric.metric_type == metric_type)
    if since:
        q = q.where(TelemetryMetric.timestamp >= since)
    if until:
        q = q.where(TelemetryMetric.timestamp <= until)
    q = q.order_by(TelemetryMetric.timestamp.desc(), TelemetryMetric.id.desc()).limit(limit).offset(offset)
    return db.execute(q).scalars().all()


@app.get("/telemetry/metric-types", response_model=list[MetricTypeOut])
def list_metric_types(user: CurrentUser, db: DB):
    return db.execute(select(TelemetryMetricType)).scalars().all()


@app.get("/telemetry/stats")
def get_stats(
    user: CurrentUser,
    db: DB,
    metric_type: str | None = None,
    instance_id: str | None = None,
    project_id: str | None = None,
    hours: int = Query(1, ge=1, le=720),
):
    M = TelemetryMetric
    q = select(
        M.metric_type, M.instance_id,
        func.max(M.unit).label("unit"),
        func.count().label("count"),
        func.avg(M.value).label("avg"),
        func.min(M.value).label("min"),
        func.max(M.value).label("max"),
        pg_array_agg(aggregate_order_by(M.value, M.timestamp.desc()))[1].label("last"),
        func.min(M.timestamp).label("first_ts"),
        func.max(M.timestamp).label("last_ts"),
    ).where(M.timestamp >= datetime.now(timezone.utc) - timedelta(hours=hours))
    if metric_type:
        q = q.where(M.metric_type == metric_type)
    if instance_id:
        q = q.where(M.instance_id == instance_id)
    scope = _scope(user, project_id)
    if scope:
        q = q.where(M.project_id == scope)
    q = q.group_by(M.metric_type, M.instance_id).order_by(M.metric_type, M.instance_id)
    return [
        {"metric_type": r.metric_type, "instance_id": r.instance_id, "unit": r.unit, "count": r.count,
         "avg": r.avg, "min": r.min, "max": r.max, "last": r.last,
         "first_ts": r.first_ts, "last_ts": r.last_ts}
        for r in db.execute(q).all()
    ]


@app.get("/telemetry/series")
def get_series(
    user: CurrentUser,
    db: DB,
    metric_type: str,
    instance_id: str,
    hours: int = Query(1, ge=1, le=720),
    granularity: Literal["raw", "minute", "hour", "day"] = "minute",
    project_id: str | None = None,
):
    M = TelemetryMetric
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    if granularity == "raw":
        q = select(M.timestamp.label("timestamp"), M.value.label("avg"), M.value.label("min"),
                   M.value.label("max"), literal(1).label("count"))
    else:
        ts = func.date_trunc(granularity, M.timestamp).label("timestamp")
        q = select(ts, func.avg(M.value).label("avg"), func.min(M.value).label("min"),
                   func.max(M.value).label("max"), func.count().label("count")).group_by(ts)
    q = q.where(M.metric_type == metric_type, M.instance_id == instance_id, M.timestamp >= cutoff)
    scope = _scope(user, project_id)
    if scope:
        q = q.where(M.project_id == scope)
    q = q.order_by(text("timestamp ASC"))
    return [{"timestamp": r.timestamp, "avg": r.avg, "min": r.min, "max": r.max, "count": r.count}
            for r in db.execute(q).all()]


@app.get("/telemetry/instances/{instance_id}/stats")
def get_instance_stats(
    user: CurrentUser,
    db: DB,
    instance_id: str,
    hours: int = Query(24, ge=1, le=720),
    granularity: str = Query("1m"),
):
    q = select(
        TelemetryMetric.metric_type,
        func.avg(TelemetryMetric.value).label("avg"),
        func.min(TelemetryMetric.value).label("min"),
        func.max(TelemetryMetric.value).label("max"),
        func.count().label("count"),
    ).where(
        TelemetryMetric.instance_id == instance_id,
        TelemetryMetric.timestamp >= datetime.now(timezone.utc) - timedelta(hours=hours),
    )
    scope = _scope(user, None)
    if scope:
        q = q.where(TelemetryMetric.project_id == scope)
    rows = db.execute(q.group_by(TelemetryMetric.metric_type)).all()
    if not rows:
        raise HTTPException(status_code=404, detail="no metrics for instance")
    metrics_map = {}
    for r in rows:
        metrics_map[r.metric_type] = {
            "avg": float(r.avg) if r.avg is not None else 0.0,
            "min": float(r.min) if r.min is not None else 0.0,
            "max": float(r.max) if r.max is not None else 0.0,
            "count": r.count,
        }
    return {"instance_id": instance_id, "metrics": metrics_map}


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics(request: Request, db: DB):
    check_metrics_token(request)
    from_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    q = select(TelemetryMetric).where(TelemetryMetric.timestamp >= from_time)
    metrics = db.execute(q).scalars().all()
    esc = lambda v: str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    lines = []
    for m in metrics:
        labels = f'instance_id="{esc(m.instance_id)}",metric_type="{esc(m.metric_type)}"'
        if m.project_id:
            labels += f',project_id="{esc(m.project_id)}"'
        lines.append(f'cloudx_metric{{{labels}}} {m.value} {int(m.timestamp.timestamp() * 1000)}')
    return "\n".join(lines) + "\n" if lines else "# no metrics\n"


@app.delete("/telemetry/metrics/cleanup", status_code=status.HTTP_204_NO_CONTENT)
def cleanup_old_metrics(user: AdminUser, db: DB):
    rollup_and_prune(db)
    return None


def rollup_and_prune(db: Session, now: datetime | None = None) -> None:
    """Fold raw samples older than the retention (cut on an hour boundary) into hourly rollups, then delete them."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=METRICS_RETENTION_DAYS)).replace(minute=0, second=0, microsecond=0)
    groups: dict[tuple, tuple[float, float, float, int]] = {}
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(724001)"))
        stmt = (
            select(
                TelemetryMetric.instance_id,
                TelemetryMetric.project_id,
                TelemetryMetric.metric_type,
                func.date_trunc("hour", TelemetryMetric.timestamp).label("bucket"),
                func.avg(TelemetryMetric.value),
                func.min(TelemetryMetric.value),
                func.max(TelemetryMetric.value),
                func.count(TelemetryMetric.id),
            )
            .where(TelemetryMetric.timestamp < cutoff)
            .group_by(
                TelemetryMetric.instance_id,
                TelemetryMetric.project_id,
                TelemetryMetric.metric_type,
                text("date_trunc('hour', timestamp)"),
            )
        )
        for row in db.execute(stmt):
            inst, proj, mt, b, avg_v, min_v, max_v, n = row
            groups[(inst, proj, mt, b)] = (float(avg_v), float(min_v), float(max_v), int(n))
    else:
        raw_groups: dict[tuple, list[float]] = {}
        for m in db.execute(select(TelemetryMetric).where(TelemetryMetric.timestamp < cutoff).execution_options(synchronize_session=False)).scalars():
            ts = m.timestamp if m.timestamp.tzinfo else m.timestamp.replace(tzinfo=timezone.utc)
            key = (m.instance_id, m.project_id, m.metric_type, ts.replace(minute=0, second=0, microsecond=0))
            raw_groups.setdefault(key, []).append(m.value)
        for key, vals in raw_groups.items():
            groups[key] = (sum(vals) / len(vals), min(vals), max(vals), len(vals))

    for (inst, proj, mt, bucket), (avg_v, min_v, max_v, n) in groups.items():
        q_ex = select(TelemetryRollup).where(
            TelemetryRollup.instance_id == inst,
            TelemetryRollup.project_id == proj if proj is not None else TelemetryRollup.project_id.is_(None),
            TelemetryRollup.metric_type == mt,
            TelemetryRollup.bucket == bucket,
        )
        existing = db.execute(q_ex).scalar_one_or_none()
        if existing:
            tot = existing.samples + n
            existing.avg_value = (existing.avg_value * existing.samples + avg_v * n) / tot
            existing.min_value = min(existing.min_value, min_v)
            existing.max_value = max(existing.max_value, max_v)
            existing.samples = tot
        else:
            db.add(TelemetryRollup(
                instance_id=inst, project_id=proj, metric_type=mt, bucket=bucket,
                avg_value=avg_v, min_value=min_v, max_value=max_v, samples=n
            ))

    db.execute(delete(TelemetryMetric).where(TelemetryMetric.timestamp < cutoff).execution_options(synchronize_session=False))
    db.execute(delete(TelemetryRollup).where(TelemetryRollup.bucket < now - timedelta(days=ROLLUP_RETENTION_DAYS)).execution_options(synchronize_session=False))
    db.commit()


@app.get("/telemetry/rollups")
def list_rollups(user: CurrentUser, db: DB, metric_type: str, instance_id: str | None = None,
                 days: int = Query(30, ge=1, le=3650), granularity: Literal["hour", "day"] = "hour"):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(TelemetryRollup).where(TelemetryRollup.metric_type == metric_type, TelemetryRollup.bucket >= since)
    if user.get("role") != "admin":
        q = q.where(TelemetryRollup.project_id == user.get("project_id"))
    if instance_id:
        q = q.where(TelemetryRollup.instance_id == instance_id)
    out: dict[datetime, dict] = {}
    for r in db.execute(q.order_by(TelemetryRollup.bucket)).scalars():
        b = r.bucket if granularity == "hour" else r.bucket.replace(hour=0)
        o = out.setdefault(b, {"bucket": b, "sum": 0.0, "min": r.min_value, "max": r.max_value, "samples": 0})
        o["sum"] += r.avg_value * r.samples
        o["samples"] += r.samples
        o["min"], o["max"] = min(o["min"], r.min_value), max(o["max"], r.max_value)
    return [{"bucket": o["bucket"], "avg": o["sum"] / o["samples"], "min": o["min"], "max": o["max"],
             "samples": o["samples"]} for o in out.values()]


# ── Alarms ────────────────────────────────────────────────────────────────────

_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
_rpc = None


class AlarmIn(BaseModel):
    name: str
    metric_type: str
    instance_id: str | None = None
    comparison: Literal[">", ">=", "<", "<="]
    threshold: float
    period: int = Field(300, ge=30, le=86400)
    evaluation_periods: int = Field(1, ge=1)
    statistic: Literal["avg", "min", "max", "sum", "count"] = "avg"
    enabled: bool = True
    project_id: str | None = None  # admin only


def alarm_dict(a: TelemetryAlarm) -> dict:
    return {c.name: getattr(a, c.name) for c in TelemetryAlarm.__table__.columns}


def _publish(a: TelemetryAlarm, old: str, value: float | None) -> None:
    global _rpc
    try:
        from shared.rpc import RPCClient
        _rpc = _rpc or RPCClient(os.getenv("RABBITMQ_URL"))
        _rpc.publish_event("telemetry.alarm.state_changed", {
            "alarm_id": a.id, "name": a.name, "project_id": a.project_id, "metric_type": a.metric_type,
            "instance_id": a.instance_id, "old_state": old, "state": a.state, "value": value,
            "comparison": a.comparison, "threshold": a.threshold})
    except Exception as exc:  # best-effort: a broker outage must not stop evaluation
        log.warning("alarm event publish failed: %s", exc)


def evaluate_alarms(db: Session, now: datetime | None = None) -> int:
    """Re-evaluate every enabled alarm; publish an event per state change. Returns the number of changes."""
    now = now or datetime.now(timezone.utc)
    changed: list[tuple[TelemetryAlarm, str, float | None]] = []
    _STAT_FUNCS = {
        "avg": func.avg,
        "min": func.min,
        "max": func.max,
        "sum": func.sum,
        "count": func.count,
    }
    for a in db.execute(select(TelemetryAlarm).where(TelemetryAlarm.enabled.is_(True))).scalars().all():
        stat_fn = _STAT_FUNCS.get(a.statistic or "avg", func.avg)(TelemetryMetric.value)
        q = select(stat_fn, func.count(TelemetryMetric.id)).where(
            TelemetryMetric.metric_type == a.metric_type, TelemetryMetric.project_id == a.project_id,
            TelemetryMetric.timestamp >= now - timedelta(seconds=a.period))
        if a.instance_id:
            q = q.where(TelemetryMetric.instance_id == a.instance_id)
        val, n = db.execute(q).one()
        if not n:
            val = None
            a.breach_count = 0
            new_state = "insufficient_data"
        else:
            val = float(val) if val is not None else 0.0
            if _OPS[a.comparison](val, a.threshold):
                a.breach_count += 1
                if a.breach_count >= (a.evaluation_periods or 1):
                    new_state = "alarm"
                else:
                    new_state = a.state
            else:
                a.breach_count = 0
                new_state = "ok"

        if new_state != a.state:
            old, a.state, a.state_updated_at = a.state, new_state, now
            stat_name = a.statistic or "avg"
            a.state_reason = f"{stat_name}={val:.2f} over {a.period}s" if n else f"no samples in {a.period}s"
            db.add(TelemetryAlarmHistory(alarm_id=a.id, old_state=old, new_state=new_state, reason=a.state_reason, value=val, at=now))
            changed.append((a, old, val))
    db.commit()
    for a, old, val in changed:
        _publish(a, old, val)
    return len(changed)


def _alarm_loop() -> None:
    while True:
        time.sleep(ALARM_INTERVAL)
        try:
            with SessionLocal() as db:
                if db.get_bind().dialect.name == "postgresql":  # one gunicorn worker per tick
                    if not db.execute(text("SELECT pg_try_advisory_lock(724002)")).scalar():
                        continue
                    try:
                        evaluate_alarms(db)
                    finally:
                        db.execute(text("SELECT pg_advisory_unlock(724002)"))
                        db.commit()
                else:
                    evaluate_alarms(db)
        except Exception:  # best-effort: keep the loop alive
            log.exception("alarm evaluation failed")


def _alarm_or_404(db: Session, alarm_id: str, user: dict) -> TelemetryAlarm:
    a = db.get(TelemetryAlarm, alarm_id)
    if not a or (user.get("role") != "admin" and a.project_id != user.get("project_id")):
        raise HTTPException(404, "alarm not found")
    return a


@app.post("/telemetry/alarms", status_code=status.HTTP_201_CREATED)
def create_alarm(user: CurrentUser, data: AlarmIn, db: DB):
    if not db.execute(select(TelemetryMetricType).where(TelemetryMetricType.name == data.metric_type)).first():
        raise HTTPException(400, f"unknown metric_type {data.metric_type}")
    project = data.project_id if user.get("role") == "admin" and data.project_id else user.get("project_id")
    if not project:
        raise HTTPException(400, "project_id required")
    a = TelemetryAlarm(**data.model_dump(exclude={"project_id"}), project_id=project)
    db.add(a)
    db.commit()
    return alarm_dict(a)


@app.get("/telemetry/alarms")
def list_alarms(user: CurrentUser, db: DB):
    q = select(TelemetryAlarm).order_by(TelemetryAlarm.created_at)
    if user.get("role") != "admin":
        q = q.where(TelemetryAlarm.project_id == user.get("project_id"))
    return [alarm_dict(a) for a in db.execute(q).scalars()]


@app.get("/telemetry/alarms/{alarm_id}")
def get_alarm(alarm_id: str, user: CurrentUser, db: DB):
    return alarm_dict(_alarm_or_404(db, alarm_id, user))


@app.get("/telemetry/alarms/{alarm_id}/history")
def get_alarm_history(alarm_id: str, user: CurrentUser, db: DB):
    alarm = _alarm_or_404(db, alarm_id, user)
    q = select(TelemetryAlarmHistory).where(TelemetryAlarmHistory.alarm_id == alarm.id).order_by(TelemetryAlarmHistory.at.desc())
    return [
        {
            "id": h.id,
            "alarm_id": h.alarm_id,
            "old_state": h.old_state,
            "new_state": h.new_state,
            "reason": h.reason,
            "value": h.value,
            "at": h.at,
        }
        for h in db.execute(q).scalars().all()
    ]


@app.delete("/telemetry/alarms/{alarm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alarm(alarm_id: str, user: CurrentUser, db: DB):
    db.delete(_alarm_or_404(db, alarm_id, user))
    db.commit()


@app.post("/telemetry/alarms/evaluate")
def evaluate_now(user: AdminUser, db: DB):
    return {"changed": evaluate_alarms(db)}


try:
    from shared.metrics import setup_metrics
    setup_metrics(app, "telemetry-api")
except Exception:  # best-effort: /metrics is optional, the API must still start
    pass

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "telemetry-api"}
