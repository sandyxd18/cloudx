import pytest


@pytest.fixture(autouse=True)
def networking_api(monkeypatch):
    monkeypatch.setenv("NETWORKING_API_URL", "http://networking.test:8001")
