from shared.schema import require_schema
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import (
    _CLOUDX_SERVICES,
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    ADMIN_PROJECT,
    ADMIN_USERNAME,
)
from .db import SessionLocal, engine
from .models import Domain, Project, ServiceCatalogEntry, ServiceEndpoint, User
from .tokens import _hash_password


# ── Bootstrap ──────────────────────────────────────────────────────────────────
def _bootstrap():
    db = SessionLocal()
    try:
        require_schema(engine, "auth")

        # ── Ensure default domain exists ──────────────────────────────────────
        default_domain = db.scalar(select(Domain).where(Domain.name == "Default"))
        if not default_domain:
            default_domain = Domain(id="default", name="Default", description="Default domain")
            db.add(default_domain)
            db.commit()
            db.refresh(default_domain)
            print("[auth] bootstrap: default domain created", flush=True)

        # ── Migrate existing projects / users to default domain ───────────────
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE auth_projects SET domain_id = 'default' WHERE domain_id IS NULL"
            ))
            conn.execute(text(
                "UPDATE auth_users SET domain_id = 'default' WHERE domain_id IS NULL"
            ))

        # ── Create admin project ──────────────────────────────────────────────
        proj = db.scalar(select(Project).where(Project.name == ADMIN_PROJECT))
        if not proj:
            proj = Project(name=ADMIN_PROJECT, description="Default admin project")
            db.add(proj)
            db.commit()
            db.refresh(proj)
        # Ensure admin project has domain_id
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE auth_projects SET domain_id = 'default' WHERE domain_id IS NULL"
            ))

        # ── Create admin user ─────────────────────────────────────────────────
        user = db.scalar(select(User).where(User.username == ADMIN_USERNAME))
        if not user:
            user = User(
                username=ADMIN_USERNAME,
                email=ADMIN_EMAIL,
                password_hash=_hash_password(ADMIN_PASSWORD),
                project_id=proj.id,
                role="admin",
            )
            db.add(user)
            db.commit()
            print(f"[auth] bootstrap: admin user '{ADMIN_USERNAME}' created in project '{ADMIN_PROJECT}'", flush=True)
        else:
            user.password_hash = _hash_password(ADMIN_PASSWORD)
            db.commit()
            print(f"[auth] bootstrap: admin user '{ADMIN_USERNAME}' password updated", flush=True)

        # ── Create service project and system service users ───────────────────
        service_proj = db.scalar(select(Project).where(Project.name == "service"))
        if not service_proj:
            service_proj = Project(name="service", description="CloudX System Service Accounts Project")
            db.add(service_proj)
            db.commit()
            db.refresh(service_proj)

        service_accounts = [
            ("service_compute", "service_compute@cloudx.local"),
            ("service_network", "service_network@cloudx.local"),
            ("service_image", "service_image@cloudx.local"),
            ("service_block_store", "service_block_store@cloudx.local"),
            ("service_load_balancer", "service_load_balancer@cloudx.local"),
            ("service_object_store", "service_object_store@cloudx.local"),
        ]
        for svc_user, svc_email in service_accounts:
            existing_svc_user = db.scalar(select(User).where(User.username == svc_user))
            if not existing_svc_user:
                db.add(User(
                    username=svc_user,
                    email=svc_email,
                    password_hash=_hash_password(ADMIN_PASSWORD),
                    project_id=service_proj.id,
                    role="admin",
                ))
        db.commit()

        # ── Bootstrap service catalog ─────────────────────────────────────────
        _bootstrap_service_catalog(db)
    finally:
        db.close()


def _bootstrap_service_catalog(db: Session) -> None:
    """Register all CloudX services into the service catalog if not already present."""
    for svc_def in _CLOUDX_SERVICES:
        existing = db.scalar(select(ServiceCatalogEntry).where(ServiceCatalogEntry.name == svc_def["name"]))
        if not existing:
            svc = ServiceCatalogEntry(
                name=svc_def["name"],
                type=svc_def["type"],
                description=f"CloudX {svc_def['name']} service",
            )
            db.add(svc)
            db.flush()  # get svc.id without full commit
            # Add public + internal endpoints
            for iface in ("public", "internal"):
                db.add(ServiceEndpoint(
                    service_id=svc.id,
                    interface=iface,
                    region="RegionOne",
                    url=svc_def["url"],
                ))
            db.commit()
            print(f"[auth] bootstrap: service catalog entry '{svc_def['name']}' registered", flush=True)
        else:
            # Ensure endpoint URLs are up to date
            endpoints = db.scalars(
                select(ServiceEndpoint).where(ServiceEndpoint.service_id == existing.id)
            ).all()
            if not endpoints:
                for iface in ("public", "internal"):
                    db.add(ServiceEndpoint(
                        service_id=existing.id,
                        interface=iface,
                        region="RegionOne",
                        url=svc_def["url"],
                    ))
                db.commit()
