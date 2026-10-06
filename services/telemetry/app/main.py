from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("telemetry-api")

"""
cloudx Telemetry API — Metrics Collection & Prometheus Exporter
================================================================

Service Phase 5.1: mengumpulkan dan mengekspos metrik real-time per VM/tenant.
"""


import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import DateTime, Float, Integer, String, create_engine, select, text, func, literal
from sqlalchemy.dialects.postgresql import array_agg as pg_array_agg, aggregate_order_by
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from shared.auth_middleware import CurrentUser, AdminUser, OptionalAuth
from shared.schema import require_schema

from shared.request_id_middleware import RequestIDMiddleware

log = logging.getLogger("telemetry-api")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx")
METRICS_RETENTION_DAYS = int(os.getenv("METRICS_RETENTION_DAYS", "7"))

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
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost").split(",") if o.strip() and o.strip() != "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
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
def prometheus_metrics(db: DB):
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
    cutoff = datetime.now(timezone.utc) - timedelta(days=METRICS_RETENTION_DAYS)
    db.execute(text("DELETE FROM telemetry_metrics WHERE timestamp < :cutoff"), {"cutoff": cutoff})
    db.commit()
    return None


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "telemetry-api"}
