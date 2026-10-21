import pytest

from cloudx_deb.manifest import load_manifest
from shared import services
from tests.stack import fresh_service


def test_table_matches_manifest():
    catalog_units = [u for u in load_manifest() if u.catalog]
    by_name = {s.name: s for s in services.SERVICES}
    for unit in catalog_units:
        assert unit.catalog in by_name, f"{unit.name}: catalog {unit.catalog!r} not in the table"
        assert by_name[unit.catalog].port == unit.port, unit.name
    for entry in services.SERVICES:
        owners = [u.name for u in catalog_units if u.kind == "api" and u.catalog == entry.name]
        assert len(owners) == 1, f"{entry.name}: manifest api units {owners}"


def test_names_unique():
    names = [s.name for s in services.SERVICES]
    assert len(names) == len(set(names)) == 22
    assert len({s.url_key for s in services.SERVICES}) == 22


def test_by_name():
    assert services.by_name("volume").url_key == "block_store_api_url"
    assert services.by_name("volume").type == "block-storage"
    with pytest.raises(KeyError):
        services.by_name("nope")


def test_public_key():
    assert services.public_key("image_api_url") == "image_api_public_url"
    assert services.public_key("auth_api_url") == "auth_api_public_url"


def test_auth_bootstrap_registers_every_service(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPUTE_API_URL", "http://compute.example:9000")
    monkeypatch.delenv("DNS_API_URL", raising=False)
    auth = fresh_service("auth", monkeypatch, tmp_path)
    registered = {s["name"]: s for s in auth.config._CLOUDX_SERVICES}
    assert list(registered) == [s.name for s in services.SERVICES]
    assert registered["compute"]["url"] == "http://compute.example:9000"
    assert registered["dns"]["url"] == "http://127.0.0.1:8016"
    for entry in services.SERVICES:
        assert registered[entry.name]["type"] == entry.type
