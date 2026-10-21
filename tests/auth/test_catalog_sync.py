import sys

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from services.auth.app.catalog_sync import sync_catalog
from services.auth.app.models import Base, ServiceCatalogEntry, ServiceEndpoint
from shared import config, manage
from shared.services import SERVICES


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'auth.sqlite'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        yield session


def endpoints(db):
    rows = db.execute(
        select(ServiceCatalogEntry.name, ServiceEndpoint.interface, ServiceEndpoint.region, ServiceEndpoint.url)
        .join(ServiceEndpoint, ServiceEndpoint.service_id == ServiceCatalogEntry.id)
    ).all()
    return {(name, iface): (region, url) for name, iface, region, url in rows}


def drop_internal_endpoints(db):
    db.execute(ServiceEndpoint.__table__.delete().where(ServiceEndpoint.interface == "internal"))
    db.commit()


def test_adds_missing_services_with_public_and_internal_endpoints(db):
    urls = {"image_api_url": "http://10.0.0.5:8002", "dns_api_url": "http://10.0.0.6:8016"}
    assert sync_catalog(db, urls) == [
        "+ image public http://10.0.0.5:8002",
        "+ image internal http://10.0.0.5:8002",
        "+ dns public http://10.0.0.6:8016",
        "+ dns internal http://10.0.0.6:8016",
    ]
    assert endpoints(db) == {
        ("image", "public"): ("RegionOne", "http://10.0.0.5:8002"),
        ("image", "internal"): ("RegionOne", "http://10.0.0.5:8002"),
        ("dns", "public"): ("RegionOne", "http://10.0.0.6:8016"),
        ("dns", "internal"): ("RegionOne", "http://10.0.0.6:8016"),
    }
    entry = db.scalar(select(ServiceCatalogEntry).where(ServiceCatalogEntry.name == "image"))
    assert (entry.type, entry.description, entry.enabled) == ("image", "CloudX image service", True)


def test_every_service_in_the_table_can_be_registered(db):
    urls = {s.url_key: f"http://10.0.0.1:{s.port}" for s in SERVICES}
    assert len(sync_catalog(db, urls)) == 2 * len(SERVICES)
    types = dict(db.execute(select(ServiceCatalogEntry.name, ServiceCatalogEntry.type)).all())
    assert types == {s.name: s.type for s in SERVICES}


def test_updates_changed_urls_and_reports_each_change(db):
    sync_catalog(db, {"image_api_url": "http://old:8002", "dns_api_url": "http://old:8016"})
    changes = sync_catalog(db, {"image_api_url": "http://new:8002", "dns_api_url": "http://old:8016"})
    assert changes == [
        "~ image public http://old:8002 -> http://new:8002",
        "~ image internal http://old:8002 -> http://new:8002",
    ]
    assert endpoints(db)[("image", "public")] == ("RegionOne", "http://new:8002")
    assert endpoints(db)[("dns", "internal")] == ("RegionOne", "http://old:8016")


def test_second_run_is_a_no_op(db):
    urls = {"image_api_url": "http://10.0.0.5:8002", "ha_api_public_url": "https://cloud/ha", "ha_api_url": "http://a:8020"}
    assert sync_catalog(db, urls)
    assert sync_catalog(db, urls) == []


def test_keys_missing_from_conf_are_left_alone(db):
    sync_catalog(db, {"image_api_url": "http://img:8002", "dns_api_url": "http://dns:8016"})
    before = endpoints(db)
    assert sync_catalog(db, {"dns_api_url": "http://dns:8016", "image_api_url": ""}) == []
    assert endpoints(db) == before


def test_public_override_key(db):
    urls = {"image_api_url": "http://10.0.0.5:8002", "image_api_public_url": "https://cloud.example/image"}
    assert sync_catalog(db, urls) == [
        "+ image public https://cloud.example/image",
        "+ image internal http://10.0.0.5:8002",
    ]
    changes = sync_catalog(db, {**urls, "image_api_public_url": "https://cloud.example/img2"})
    assert changes == ["~ image public https://cloud.example/image -> https://cloud.example/img2"]
    assert endpoints(db)[("image", "internal")] == ("RegionOne", "http://10.0.0.5:8002")


def test_comma_list_registers_the_first_url(db):
    assert sync_catalog(db, {"ha_api_url": " http://a:8020 , http://b:8020"}) == [
        "+ ha public http://a:8020",
        "+ ha internal http://a:8020",
    ]
    assert sync_catalog(db, {"ha_api_url": "http://a:8020,http://b:8020"}) == []


def test_missing_interface_is_created(db):
    sync_catalog(db, {"image_api_url": "http://img:8002"})
    drop_internal_endpoints(db)
    assert sync_catalog(db, {"image_api_url": "http://img:8002"}) == ["+ image internal http://img:8002"]
    assert ("image", "internal") in endpoints(db)


def test_entry_with_a_different_type_is_left_as_is(db):
    db.add(ServiceCatalogEntry(name="image", type="custom"))
    db.commit()
    sync_catalog(db, {"image_api_url": "http://img:8002"})
    assert db.scalar(select(ServiceCatalogEntry.type).where(ServiceCatalogEntry.name == "image")) == "custom"
    assert endpoints(db)[("image", "public")] == ("RegionOne", "http://img:8002")


def test_dry_run_changes_nothing(db):
    sync_catalog(db, {"image_api_url": "http://old:8002"})
    before = endpoints(db)
    urls = {"image_api_url": "http://new:8002", "dns_api_url": "http://dns:8016"}
    expected = sync_catalog(db, urls, dry_run=True)
    assert expected == [
        "~ image public http://old:8002 -> http://new:8002",
        "~ image internal http://old:8002 -> http://new:8002",
        "+ dns public http://dns:8016",
        "+ dns internal http://dns:8016",
    ]
    assert not db.dirty and not db.new
    db.rollback()
    assert endpoints(db) == before
    assert db.scalar(select(ServiceCatalogEntry).where(ServiceCatalogEntry.name == "dns")) is None
    assert sync_catalog(db, urls) == expected  # a real run reports the same lines


IMAGE = {"image_api_url": "http://new:8002"}


def add_image(db, *endpoint_specs):
    """An existing `image` entry with (interface, region, url) endpoints."""
    entry = ServiceCatalogEntry(name="image", type="image")
    db.add(entry)
    db.flush()
    for interface, region, url in endpoint_specs:
        db.add(ServiceEndpoint(service_id=entry.id, interface=interface, region=region, url=url))
    db.commit()


def test_create_only_adds_new_services_but_never_updates_existing(db):
    sync_catalog(db, {"image_api_url": "http://old:8002"})
    urls = {**IMAGE, "dns_api_url": "http://dns:8016"}
    assert sync_catalog(db, urls, create_only=True) == [
        "+ dns public http://dns:8016",
        "+ dns internal http://dns:8016",
    ]
    assert endpoints(db)[("image", "public")] == ("RegionOne", "http://old:8002")
    assert sync_catalog(db, urls, create_only=True) == []


def test_create_only_leaves_an_entry_with_only_public_alone(db):
    add_image(db, ("public", "RegionOne", "http://pub:8002"))
    assert sync_catalog(db, IMAGE, create_only=True) == []
    assert endpoints(db) == {("image", "public"): ("RegionOne", "http://pub:8002")}


def test_create_only_does_not_restore_a_deleted_endpoint(db):
    sync_catalog(db, IMAGE)
    drop_internal_endpoints(db)
    assert sync_catalog(db, IMAGE, create_only=True) == []
    assert set(endpoints(db)) == {("image", "public")}


def test_create_only_fills_an_entry_without_endpoints(db):
    add_image(db)
    assert sync_catalog(db, IMAGE, create_only=True) == [
        "+ image public http://new:8002",
        "+ image internal http://new:8002",
    ]
    assert set(endpoints(db)) == {("image", "public"), ("image", "internal")}


def test_sync_still_adds_the_missing_interface_of_an_entry_with_only_public(db):
    add_image(db, ("public", "RegionOne", "http://new:8002"))
    assert sync_catalog(db, IMAGE) == ["+ image internal http://new:8002"]
    assert set(endpoints(db)) == {("image", "public"), ("image", "internal")}


def test_create_only_leaves_an_entry_with_endpoints_in_another_region_alone(db):
    add_image(db, ("public", "RegionTwo", "http://r2:8002"), ("internal", "RegionTwo", "http://r2:8002"))
    assert sync_catalog(db, IMAGE, create_only=True) == []
    assert set(db.scalars(select(ServiceEndpoint.region)).all()) == {"RegionTwo"}


# ── cloudx-manage catalog sync ────────────────────────────────────────────────

def _forget_auth_modules():
    for name in [n for n in sys.modules if n == "auth" or n.startswith("auth.")]:
        del sys.modules[name]


@pytest.fixture
def manage_env(tmp_path, monkeypatch):
    """A conf dir plus an empty sqlite auth database; `auth.app` (the services-directory spelling) imports fresh."""
    etc = tmp_path / "etc"
    etc.mkdir()
    db_path = tmp_path / "auth.sqlite"
    (etc / "cloudx.conf").write_text(
        "[DEFAULT]\n"
        "auth_secret_key = test-secret-key-not-for-production-32b\n"
        "auth_admin_password = test-admin-password\n"
        f"database_url = sqlite:///{db_path}\n"
        "image_api_url = http://10.0.0.5:8002\n"
        "image_api_public_url = https://cloud.example/image\n"
    )
    monkeypatch.setattr(config, "ETC_CLOUDX_DIR", etc)
    monkeypatch.setenv("CLOUDX_SERVICES_DIR", str(manage.migrate.services_dir()))
    monkeypatch.setattr(sys, "path", list(sys.path))
    _forget_auth_modules()
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    def rows():
        with sessionmaker(bind=engine)() as session:
            return endpoints(session)

    yield rows
    _forget_auth_modules()
    engine.dispose()


def test_catalog_sync_command(manage_env, capsys):
    assert manage.main(["catalog", "sync", "--dry-run"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["+ image public https://cloud.example/image", "+ image internal http://10.0.0.5:8002"]
    assert manage_env() == {}

    assert manage.main(["catalog", "sync"]) == 0
    assert capsys.readouterr().out.splitlines() == out
    assert manage_env() == {
        ("image", "public"): ("RegionOne", "https://cloud.example/image"),
        ("image", "internal"): ("RegionOne", "http://10.0.0.5:8002"),
    }

    assert manage.main(["catalog", "sync"]) == 0
    assert capsys.readouterr().out.strip() == "catalog already up to date"


def test_bootstrap_registers_services_but_never_overwrites(monkeypatch, tmp_path):
    from tests.stack import fresh_service

    monkeypatch.setenv("IMAGE_API_URL", "http://10.0.0.5:8002")
    auth = fresh_service("auth", monkeypatch, tmp_path)
    with auth.SessionLocal() as session:
        auth._bootstrap_service_catalog(session)
        rows = endpoints(session)
        assert len(rows) == 2 * len(SERVICES)
        assert rows[("image", "public")] == rows[("image", "internal")] == ("RegionOne", "http://10.0.0.5:8002")
        assert rows[("dns", "public")] == ("RegionOne", "http://127.0.0.1:8016")

        session.execute(ServiceEndpoint.__table__.update().where(ServiceEndpoint.url == "http://10.0.0.5:8002")
                        .values(url="https://edited.example/image"))
        session.commit()
        auth._bootstrap_service_catalog(session)
        assert endpoints(session)[("image", "public")] == ("RegionOne", "https://edited.example/image")


def test_catalog_sync_needs_the_auth_secrets(manage_env, monkeypatch):
    (config.ETC_CLOUDX_DIR / "cloudx.conf").write_text("[DEFAULT]\n")
    monkeypatch.delenv("AUTH_SECRET_KEY")
    with pytest.raises(SystemExit) as exc:
        manage.main(["catalog", "sync"])
    assert "auth_secret_key" in str(exc.value)
