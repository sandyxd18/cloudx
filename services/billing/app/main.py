from __future__ import annotations
import os
from shared.config import load_service_config
load_service_config("billing-api")

"""
cloudx Billing API — Rating, Usage, Invoicing & PDF
====================================================

Service Fase 5.2: rating sheet, usage records, invoice generation, PDF export.
"""


import base64
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import Template
from pydantic import BaseModel
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text,
    create_engine, select, text
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from shared.auth_middleware import CurrentUser, AdminUser
from shared.schema import require_schema

from shared.request_id_middleware import RequestIDMiddleware

try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except (ImportError, OSError):
    WEASYPRINT_AVAILABLE = False

log = logging.getLogger("billing-api")

DATABASE_URL = os.getenv("BILLING_DATABASE_URL", os.getenv("DATABASE_URL", "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx_billing"))
AUTH_API_URL = os.getenv("AUTH_API_URL", "http://auth-api:8000").rstrip("/")
AUTH_SECRET_KEY = os.environ["AUTH_SECRET_KEY"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class RatingSheet(Base):
    __tablename__ = "billing_rating_sheets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    rate_per_unit: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="IDR")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UsageRecord(Base):
    __tablename__ = "billing_usage_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    rate_per_unit: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Invoice(Base):
    __tablename__ = "billing_invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(15, 4), default=Decimal("0.00"))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.11"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 4), default=Decimal("0.00"))
    total: Mapped[Decimal] = mapped_column(Numeric(15, 4), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    items: Mapped[list["InvoiceItem"]] = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceItem(Base):
    __tablename__ = "billing_invoice_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(Integer, ForeignKey("billing_invoices.id"), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    rate_per_unit: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="items")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


def init_db():
    require_schema(engine, "billing")
    defaults = [
        RatingSheet(resource_type="vcpu", unit="vcpu-hour", rate_per_unit=Decimal("100.0000"), description="vCPU per hour", currency="IDR"),
        RatingSheet(resource_type="ram", unit="gb-hour", rate_per_unit=Decimal("50.0000"), description="RAM GB per hour", currency="IDR"),
        RatingSheet(resource_type="volume", unit="gb-hour", rate_per_unit=Decimal("25.0000"), description="Volume GB per hour", currency="IDR"),
        RatingSheet(resource_type="floating_ip", unit="hour", rate_per_unit=Decimal("500.0000"), description="Floating IP per hour", currency="IDR"),
    ]
    with SessionLocal() as db:
        if db.get_bind().dialect.name == "postgresql":
            # every gunicorn worker runs init_db at startup; without the lock they all see an empty table and seed it
            db.execute(text("SELECT pg_advisory_xact_lock(5011)"))
        existing = set(db.execute(select(RatingSheet.resource_type)).scalars().all())
        for rs in defaults:
            if rs.resource_type not in existing:
                db.add(rs)
        db.commit()
    log.info("Billing default rating sheet seeded")


app = FastAPI(
    title="CloudX Billing API",
    description="Rating, usage, invoicing and PDF export for CloudX",
    version="5.2.0",
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
    log.info("Billing API started")


# ── Pydantic models ───────────────────────────────────────────────────────────

class RatingSheetIn(BaseModel):
    resource_type: str
    unit: str
    rate_per_unit: Decimal
    currency: str = "IDR"
    description: str | None = None
    is_active: bool = True


class RatingSheetOut(BaseModel):
    id: int
    resource_type: str
    unit: str
    rate_per_unit: Decimal
    currency: str
    description: str | None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class UsageRecordIn(BaseModel):
    project_id: str
    resource_type: str
    resource_id: str | None = None
    quantity: Decimal
    unit: str
    rate_per_unit: Decimal
    period_start: datetime
    period_end: datetime


class UsageRecordOut(BaseModel):
    id: int
    project_id: str
    resource_type: str
    resource_id: str | None
    quantity: Decimal
    unit: str
    rate_per_unit: Decimal
    total_cost: Decimal
    period_start: datetime
    period_end: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class InvoiceItemIn(BaseModel):
    resource_type: str
    resource_id: str | None = None
    description: str | None = None
    quantity: Decimal
    unit: str
    rate_per_unit: Decimal
    total: Decimal


class InvoiceOut(BaseModel):
    id: int
    invoice_number: str
    project_id: str
    period_start: datetime
    period_end: datetime
    subtotal: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class InvoiceDetailOut(InvoiceOut):
    items: list[dict]


class BillingSummary(BaseModel):
    project_id: str
    period_start: datetime
    period_end: datetime
    total_vcpu_hours: Decimal = Decimal("0")
    total_ram_gb_hours: Decimal = Decimal("0")
    total_volume_gb_hours: Decimal = Decimal("0")
    total_floating_ip_hours: Decimal = Decimal("0")
    estimated_cost: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    currency: str = "IDR"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _next_invoice_number(db: Session) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    count = db.execute(select(Invoice).where(Invoice.invoice_number.like(f"INV-{today}-%"))).scalars().all()
    return f"INV-{today}-{len(count) + 1:04d}"


# ── Rating Sheets ─────────────────────────────────────────────────────────────

@app.post("/billing/rating-sheets", response_model=RatingSheetOut, status_code=status.HTTP_201_CREATED)
def create_rating_sheet(user: AdminUser, data: RatingSheetIn, db: DB):
    rs = RatingSheet(**data.model_dump())
    db.add(rs)
    db.commit()
    db.refresh(rs)
    return rs


@app.get("/billing/rating-sheets", response_model=list[RatingSheetOut])
def list_rating_sheets(user: CurrentUser, db: DB, active_only: bool = True, resource_type: str | None = None):
    q = select(RatingSheet)
    if active_only:
        q = q.where(RatingSheet.is_active == True)
    if resource_type:
        q = q.where(RatingSheet.resource_type == resource_type)
    return db.execute(q).scalars().all()


@app.get("/billing/rating-sheets/{sheet_id}", response_model=RatingSheetOut)
def get_rating_sheet(user: CurrentUser, sheet_id: int, db: DB):
    rs = db.get(RatingSheet, sheet_id)
    if not rs:
        raise HTTPException(status_code=404, detail="Rating sheet not found")
    return rs


@app.patch("/billing/rating-sheets/{sheet_id}", response_model=RatingSheetOut)
def update_rating_sheet(user: AdminUser, sheet_id: int, data: RatingSheetIn, db: DB):
    rs = db.get(RatingSheet, sheet_id)
    if not rs:
        raise HTTPException(status_code=404, detail="Rating sheet not found")
    for field, value in data.model_dump().items():
        setattr(rs, field, value)
    db.commit()
    db.refresh(rs)
    return rs


# ── Usage Records ─────────────────────────────────────────────────────────────

@app.post("/billing/usage-records", response_model=UsageRecordOut, status_code=status.HTTP_201_CREATED)
def create_usage_record(user: CurrentUser, data: UsageRecordIn, db: DB):
    total = data.quantity * data.rate_per_unit
    rec = UsageRecord(**data.model_dump(), total_cost=total)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


@app.get("/billing/usage-records", response_model=list[UsageRecordOut])
def list_usage_records(
    user: CurrentUser,
    db: DB,
    project_id: str | None = None,
    resource_type: str | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
):
    q = select(UsageRecord)
    if project_id:
        q = q.where(UsageRecord.project_id == project_id)
    if resource_type:
        q = q.where(UsageRecord.resource_type == resource_type)
    if period_start:
        q = q.where(UsageRecord.period_start >= period_start)
    if period_end:
        q = q.where(UsageRecord.period_end <= period_end)
    q = q.order_by(UsageRecord.created_at.desc()).limit(limit)
    return db.execute(q).scalars().all()


# ── Invoices ──────────────────────────────────────────────────────────────────

@app.post("/billing/invoices", response_model=InvoiceDetailOut, status_code=status.HTTP_201_CREATED)
def create_invoice(
    user: CurrentUser,
    db: DB,
    project_id: str | None = None,
    year: int | None = None,
    month: int | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    notes: str | None = None,
):
    pid = project_id or (user.get("project_id") if isinstance(user, dict) else getattr(user, "project_id", "admin")) or "admin"
    now = datetime.now(timezone.utc)
    p_start = period_start or (now - timedelta(days=30))
    p_end = period_end or now

    # Aggregate usage for project in period
    usages = db.execute(
        select(UsageRecord).where(
            UsageRecord.project_id == pid,
            UsageRecord.period_start >= p_start,
            UsageRecord.period_end <= p_end,
        )
    ).scalars().all()

    items = []
    subtotal = Decimal("0.00")
    for u in usages:
        item_total = u.total_cost
        subtotal += item_total
        items.append({
            "resource_type": u.resource_type,
            "resource_id": u.resource_id,
            "description": f"Usage {u.resource_type} ({u.period_start} - {u.period_end})",
            "quantity": float(u.quantity),
            "unit": u.unit,
            "rate_per_unit": float(u.rate_per_unit),
            "total": float(item_total),
        })

    tax_rate = Decimal("0.11")
    tax_amount = subtotal * tax_rate
    total = subtotal + tax_amount

    invoice = Invoice(
        invoice_number=_next_invoice_number(db),
        project_id=pid,
        period_start=p_start,
        period_end=p_end,
        subtotal=subtotal,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        total=total,
        status="draft",
        notes=notes,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    for it in items:
        db.add(InvoiceItem(invoice_id=invoice.id, **it))
    db.commit()

    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "project_id": invoice.project_id,
        "period_start": invoice.period_start,
        "period_end": invoice.period_end,
        "subtotal": invoice.subtotal,
        "tax_rate": invoice.tax_rate,
        "tax_amount": invoice.tax_amount,
        "total": invoice.total,
        "status": invoice.status,
        "notes": invoice.notes,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
        "items": items,
    }


@app.get("/billing/invoices", response_model=list[InvoiceOut])
def list_invoices(user: CurrentUser, db: DB, project_id: str | None = None, status: str | None = None):
    q = select(Invoice)
    if project_id:
        q = q.where(Invoice.project_id == project_id)
    if status:
        q = q.where(Invoice.status == status)
    q = q.order_by(Invoice.created_at.desc())
    return db.execute(q).scalars().all()


@app.get("/billing/invoices/{invoice_id}", response_model=InvoiceDetailOut)
def get_invoice(user: CurrentUser, invoice_id: int, db: DB):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    items = [{c.name: getattr(i, c.name) for c in i.__table__.columns} for i in inv.items]
    return {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "project_id": inv.project_id,
        "period_start": inv.period_start,
        "period_end": inv.period_end,
        "subtotal": inv.subtotal,
        "tax_rate": inv.tax_rate,
        "tax_amount": inv.tax_amount,
        "total": inv.total,
        "status": inv.status,
        "notes": inv.notes,
        "created_at": inv.created_at,
        "updated_at": inv.updated_at,
        "items": items,
    }


@app.post("/billing/invoices/{invoice_id}/pdf")
def generate_invoice_pdf(user: CurrentUser, invoice_id: int, db: DB):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    items = [{c.name: getattr(i, c.name) for c in i.__table__.columns} for i in inv.items]

    html = Template("""
    <html><body>
    <h1>Invoice {{ invoice.invoice_number }}</h1>
    <p>Project: {{ invoice.project_id }}</p>
    <p>Period: {{ invoice.period_start }} - {{ invoice.period_end }}</p>
    <table border="1">
      <tr><th>Resource</th><th>Qty</th><th>Unit</th><th>Rate</th><th>Total</th></tr>
      {% for item in items %}
      <tr><td>{{ item.resource_type }}</td><td>{{ item.quantity }}</td><td>{{ item.unit }}</td><td>{{ item.rate_per_unit }}</td><td>{{ item.total }}</td></tr>
      {% endfor %}
    </table>
    <p>Subtotal: {{ invoice.subtotal }}</p>
    <p>Tax: {{ invoice.tax_amount }}</p>
    <p>Total: {{ invoice.total }}</p>
    </body></html>
    """).render(invoice=inv, items=items)

    if WEASYPRINT_AVAILABLE:
        pdf_bytes = HTML(string=html).write_pdf()
        return {"invoice_id": invoice_id, "pdf_base64": base64.b64encode(pdf_bytes).decode()}
    return {"invoice_id": invoice_id, "html": html, "note": "weasyprint not available, returning HTML"}


@app.get("/billing/summary", response_model=BillingSummary)
@app.get("/billing/summary/{project_id}", response_model=BillingSummary)
def billing_summary(
    user: CurrentUser,
    db: DB,
    project_id: str | None = None,
    year: int | None = None,
    month: int | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
):
    pid = project_id or (user.get("project_id") if isinstance(user, dict) else getattr(user, "project_id", "admin")) or "admin"
    now = datetime.now(timezone.utc)
    p_start = period_start or (now - timedelta(days=30))
    p_end = period_end or now

    usages = db.execute(
        select(UsageRecord).where(
            UsageRecord.project_id == pid,
            UsageRecord.period_start >= p_start,
            UsageRecord.period_end <= p_end,
        )
    ).scalars().all()

    summary = BillingSummary(project_id=pid, period_start=p_start, period_end=p_end)
    total_cost = Decimal("0")
    for u in usages:
        total_cost += u.total_cost
        if u.resource_type == "vcpu":
            summary.total_vcpu_hours += u.quantity
        elif u.resource_type == "ram":
            summary.total_ram_gb_hours += u.quantity
        elif u.resource_type == "volume":
            summary.total_volume_gb_hours += u.quantity
        elif u.resource_type == "floating_ip":
            summary.total_floating_ip_hours += u.quantity
    summary.estimated_cost = total_cost
    summary.total_cost = total_cost
    return summary


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "billing-api"}
