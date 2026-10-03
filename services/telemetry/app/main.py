from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
try:
    from shared.config import load_service_config
    load_service_config("telemetry-api")
except Exception as _cfg_err:
    pass

"""
cloudx Telemetry API — Metrics Collection & Prometheus Exporter
================================================================

Service Fase 5.1: mengumpulkan dan mengekspos metrik real-time per VM/tenant.
"""


import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, Integer, String, create_engine, select, text, inspect, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

sys.path.insert(0, "/app")
from shared.auth_middleware import CurrentUser, AdminUser, OptionalAuth

try:
    from shared.request_id_middleware import RequestIDMiddleware
except ImportError:
    RequestIDMiddleware = None

log = logging.getLogger("telemetry-api")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://mini:mini@postgres:5432/mini_openstack")
METRICS_RETENTION_DAYS = int(os.getenv("METRICS_RETENTION_DAYS", "7"))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
inspector = inspect(engine)


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
    tables = [TelemetryMetric.__tablename__, TelemetryMetricType.__tablename__]
    if not all(inspector.has_table(t) for t in tables):
        Base.metadata.create_all(bind=engine)
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
            existing = {r.name for r in db.execute(select(TelemetryMetricType.name)).scalars().all()}
            for mt in defaults:
                if mt.name not in existing:
                    db.add(mt)
            db.commit()
        log.info("Telemetry tables created and metric types seeded")


app = FastAPI(
    title="CloudX Telemetry API",
    description="Metrics collection and Prometheus exporter for CloudX",
    version="5.1.0",
)

if RequestIDMiddleware:
    app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


class StatsQuery(BaseModel):
    instance_id: str | None = None
    project_id: str | None = None
    metric_type: str | None = None
    from_time: datetime | None = None
    to_time: datetime | None = None
    granularity: str = "1m"


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.post("/telemetry/metrics", response_model=MetricOut, status_code=status.HTTP_201_CREATED)
def create_metric(user: CurrentUser, data: MetricIn, db: DB):
    metric = TelemetryMetric(**data.model_dump())
    db.add(metric)
    db.commit()
    db.refresh(metric)
    return metric


@app.post("/telemetry/metrics/batch", status_code=status.HTTP_201_CREATED)
def create_metrics_batch(user: CurrentUser, data: list[MetricIn], db: DB):
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
    from_time: datetime | None = None,
    to_time: datetime | None = None,
    limit: int = Query(1000, ge=1, le=10000),
):
    q = select(TelemetryMetric)
    if instance_id:
        q = q.where(TelemetryMetric.instance_id == instance_id)
    if project_id:
        q = q.where(TelemetryMetric.project_id == project_id)
    if metric_type:
        q = q.where(TelemetryMetric.metric_type == metric_type)
    if from_time:
        q = q.where(TelemetryMetric.timestamp >= from_time)
    if to_time:
        q = q.where(TelemetryMetric.timestamp <= to_time)
    q = q.order_by(TelemetryMetric.timestamp.desc()).limit(limit)
    return db.execute(q).scalars().all()


@app.get("/telemetry/metric-types", response_model=list[MetricTypeOut])
def list_metric_types(user: OptionalAuth, db: DB):
    return db.execute(select(TelemetryMetricType)).scalars().all()


@app.get("/telemetry/stats")
def get_stats(user: CurrentUser, db: DB, query: StatsQuery = Depends()):
    q = select(
        TelemetryMetric.metric_type,
        func.avg(TelemetryMetric.value).label("avg"),
        func.min(TelemetryMetric.value).label("min"),
        func.max(TelemetryMetric.value).label("max"),
        func.count().label("count"),
    )
    if query.instance_id:
        q = q.where(TelemetryMetric.instance_id == query.instance_id)
    if query.project_id:
        q = q.where(TelemetryMetric.project_id == query.project_id)
    if query.metric_type:
        q = q.where(TelemetryMetric.metric_type == query.metric_type)
    if query.from_time:
        q = q.where(TelemetryMetric.timestamp >= query.from_time)
    if query.to_time:
        q = q.where(TelemetryMetric.timestamp <= query.to_time)
    q = q.group_by(TelemetryMetric.metric_type)
    rows = db.execute(q).all()
    return [{"metric_type": r.metric_type, "avg": r.avg, "min": r.min, "count": r.count} for r in rows]


@app.get("/telemetry/instances/{instance_id}/stats")
def get_instance_stats(
    user: CurrentUser,
    db: DB,
    instance_id: str,
    hours: int = Query(24),
    granularity: str = Query("1m"),
):
    q = select(
        TelemetryMetric.metric_type,
        func.avg(TelemetryMetric.value).label("avg"),
        func.min(TelemetryMetric.value).label("min"),
        func.max(TelemetryMetric.value).label("max"),
        func.count().label("count"),
    ).where(TelemetryMetric.instance_id == instance_id).group_by(TelemetryMetric.metric_type)
    rows = db.execute(q).all()
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
    lines = []
    for m in metrics:
        labels = f'instance_id="{m.instance_id}",metric_type="{m.metric_type}"'
        if m.project_id:
            labels += f',project_id="{m.project_id}"'
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
