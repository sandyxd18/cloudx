import pytest
from shared.db import get_db_url


def test_get_db_url_with_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")
    assert get_db_url("mydb") == "postgresql+psycopg://user:pass@localhost:5432/testdb"


def test_get_db_url_unset_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CLOUDX_ALLOW_DEFAULT_DB", raising=False)
    with pytest.raises(RuntimeError, match="Database configuration error"):
        get_db_url("mydb")


def test_get_db_url_unset_with_opt_in(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CLOUDX_ALLOW_DEFAULT_DB", "1")
    assert get_db_url("cloudx_test") == "postgresql+psycopg://cloudx:cloudx@127.0.0.1:5432/cloudx_test"
