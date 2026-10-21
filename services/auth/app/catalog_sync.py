"""Make the service catalog match the service URLs from the conf (`cloudx-manage catalog sync`)."""
from collections.abc import Mapping

from shared.services import SERVICES, public_key
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ServiceCatalogEntry, ServiceEndpoint

REGION = "RegionOne"


def _first_url(value: str | None) -> str:
    """`http://a:8020, http://b:8020` (a failover list) registers only the first URL."""
    return (value or "").split(",")[0].strip()


def sync_catalog(db: Session, urls: Mapping[str, str], *, dry_run: bool = False, create_only: bool = False) -> list[str]:
    """Add and update catalog endpoints from `urls` (lowercase conf key -> URL); returns one line per change.

    A service whose key is absent or empty in `urls` is left alone, and nothing is ever deleted.
    `create_only` only adds what is missing, `dry_run` reports without writing.
    """
    changes: list[str] = []
    for svc in SERVICES:
        internal = _first_url(urls.get(svc.url_key))
        if not internal:
            continue
        wanted = {"public": _first_url(urls.get(public_key(svc.url_key))) or internal, "internal": internal}

        entry = db.scalar(select(ServiceCatalogEntry).where(ServiceCatalogEntry.name == svc.name))
        if entry is None and not dry_run:
            entry = ServiceCatalogEntry(name=svc.name, type=svc.type, description=f"CloudX {svc.name} service")
            db.add(entry)
            db.flush()
        current = {}
        if entry is not None:
            for ep in db.scalars(select(ServiceEndpoint)
                                 .where(ServiceEndpoint.service_id == entry.id, ServiceEndpoint.region == REGION)
                                 .order_by(ServiceEndpoint.created_at, ServiceEndpoint.id)):
                current.setdefault(ep.interface, ep)

        for interface, url in wanted.items():
            ep = current.get(interface)
            if ep is None:
                changes.append(f"+ {svc.name} {interface} {url}")
                if not dry_run:
                    db.add(ServiceEndpoint(service_id=entry.id, interface=interface, region=REGION, url=url))
            elif ep.url != url and not create_only:
                changes.append(f"~ {svc.name} {interface} {ep.url} -> {url}")
                if not dry_run:
                    ep.url = url
    if dry_run:
        return changes
    db.commit()
    return changes
