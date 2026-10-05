import importlib
import re
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from shared.migrate import load_registry, main, upgrade
from shared.schema import require_schema, version_table
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2] / "services"

REGISTRY = load_registry(ROOT).SERVICES

SERVICES = sorted(REGISTRY)

def models_metadata(svc):
    return importlib.import_module(REGISTRY[svc][3]).Base.metadata

def sqlite_url(tmp_path):
    return f"sqlite:///{tmp_path / 'db.sqlite'}"

def run(url, *statements):
    with create_engine(url).begin() as conn:
        for sql in statements:
            conn.execute(text(sql))

def scalar(url, sql):
    with create_engine(url).connect() as conn:
        return conn.execute(text(sql)).scalar()

def columns(url, table):
    return {c["name"] for c in inspect(create_engine(url)).get_columns(table)}

@pytest.mark.parametrize("svc", SERVICES)
def test_baseline_matches_models(svc, tmp_path):
    """A freshly migrated database has exactly the tables, columns, indexes and keys the models declare."""
    url = sqlite_url(tmp_path)
    upgrade(svc, url, ROOT)
    engine = create_engine(url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"version_table": version_table(svc), "compare_type": True})
        assert compare_metadata(ctx, models_metadata(svc)) == []
        assert conn.execute(text(f"SELECT version_num FROM {version_table(svc)}")).scalar() == f"{svc}_0001"

@pytest.mark.parametrize("svc", SERVICES)
def test_upgrade_adopts_database_created_by_old_create_all(svc, tmp_path):
    """Existing installs (schema built by the services' former create_all) upgrade cleanly and keep their data."""
    url = sqlite_url(tmp_path)
    engine = create_engine(url)
    models_metadata(svc).create_all(engine)
    upgrade(svc, url, ROOT)
    upgrade(svc, url, ROOT)  # running it again is a no-op
    require_schema(engine, svc)

def test_services_never_touch_schema():
    offenders = []
    for path in ROOT.glob("*/app/**/*.py"):
        if re.search(r"\bcreate_all\(|\bensure_columns\(|ALTER TABLE|CREATE TABLE", path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []

def test_require_schema_fails_fast_until_migrated(tmp_path):
    url = sqlite_url(tmp_path)
    engine = create_engine(url)
    with pytest.raises(RuntimeError, match="cloudx-db-migrate k8s_engine"):
        require_schema(engine, "k8s_engine")
    upgrade("k8s_engine", url, ROOT)
    require_schema(engine, "k8s_engine")

def test_cli_upgrades_one_service_and_reports_current(tmp_path, capfd):
    url = sqlite_url(tmp_path)
    main(["--services-dir", str(ROOT), "--url", url, "key_manager"])
    main(["--services-dir", str(ROOT), "--url", url, "--current", "key_manager"])
    assert "key_manager_0001" in capfd.readouterr().out
    assert inspect(create_engine(url)).get_table_names()

def test_compute_legacy_database_is_brought_up_to_date(tmp_path):
    url = sqlite_url(tmp_path)
    run(url,
        "CREATE TABLE compute_servers (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, flavor VARCHAR, "
        "status VARCHAR, backend VARCHAR)",
        "INSERT INTO compute_servers (id, name, flavor, status, backend) VALUES ('s1', 'vm1', 'tiny', 'ACTIVE', 'simulator')")
    upgrade("compute", url, ROOT)
    assert columns(url, "compute_servers") >= {"flavor_name", "locked", "keypair_name", "bootable", "original_ram_mb"}
    assert scalar(url, "SELECT backend FROM compute_servers WHERE id = 's1'") == "libvirt"
    assert scalar(url, "SELECT flavor_name FROM compute_servers WHERE id = 's1'") == "tiny"
    assert scalar(url, "SELECT locked FROM compute_servers WHERE id = 's1'") in (0, False)

def test_networking_legacy_database_is_brought_up_to_date(tmp_path):
    url = sqlite_url(tmp_path)
    run(url,
        "CREATE TABLE networking_subnets (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, network_id VARCHAR, "
        "cidr VARCHAR NOT NULL, gateway VARCHAR, dhcp_options_id VARCHAR)",
        "INSERT INTO networking_subnets (id, name, network_id, cidr, gateway, dhcp_options_id) "
        "VALUES ('sub1', 's', 'n1', '10.0.0.0/24', '10.0.0.1', 'dhcp-uuid')")
    upgrade("networking", url, ROOT)
    assert columns(url, "networking_subnets") >= {"gateway_ip", "ovn_dhcp_options_uuid", "ip_version", "enable_dhcp"}
    assert scalar(url, "SELECT gateway_ip FROM networking_subnets WHERE id = 'sub1'") == "10.0.0.1"
    assert scalar(url, "SELECT ovn_dhcp_options_uuid FROM networking_subnets WHERE id = 'sub1'") == "dhcp-uuid"
    assert scalar(url, "SELECT ip_version FROM networking_subnets WHERE id = 'sub1'") == 4

def test_block_store_legacy_database_is_brought_up_to_date(tmp_path):
    url = sqlite_url(tmp_path)
    run(url,
        "CREATE TABLE block_store_volumes (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, size_gb INTEGER NOT NULL, "
        "status VARCHAR NOT NULL)",
        "INSERT INTO block_store_volumes (id, name, size_gb, status) VALUES ('v1', 'vol', 1, 'available')")
    upgrade("block_store", url, ROOT)
    assert columns(url, "block_store_volumes") >= {"bootable", "backend", "multiattach", "replication_status"}
    assert scalar(url, "SELECT backend FROM block_store_volumes WHERE id = 'v1'") == "lvm"
    assert scalar(url, "SELECT bootable FROM block_store_volumes WHERE id = 'v1'") in (0, False)

def test_image_legacy_database_is_brought_up_to_date(tmp_path):
    url = sqlite_url(tmp_path)
    run(url,
        "CREATE TABLE image_images (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, filename VARCHAR NOT NULL)",
        "INSERT INTO image_images (id, name, filename) VALUES ('i1', 'cirros', 'cirros.qcow2')")
    upgrade("image", url, ROOT)
    assert scalar(url, "SELECT object_name FROM image_images WHERE id = 'i1'") == "cirros.qcow2"
    assert scalar(url, "SELECT visibility FROM image_images WHERE id = 'i1'") == "private"
    assert scalar(url, "SELECT storage_backend FROM image_images WHERE id = 'i1'") == "file"

@pytest.mark.parametrize("svc, table", [("billing", "billing_rating_sheets"), ("telemetry", "telemetry_metric_types")])
def test_default_data_seeding_is_idempotent(svc, table, tmp_path, monkeypatch):
    import sys

    url = sqlite_url(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    for name in [n for n in sys.modules if n.startswith(f"services.{svc}.app")]:
        del sys.modules[name]
    mod = importlib.import_module(f"services.{svc}.app.main")
    upgrade(svc, url, ROOT)
    mod.init_db()
    first = scalar(url, f"SELECT count(*) FROM {table}")
    mod.init_db()  # restart against a populated database
    assert first > 0 and scalar(url, f"SELECT count(*) FROM {table}") == first
