import os
from pathlib import Path

from services.shared.config import load_service_config


def test_load_service_config_from_file(tmp_path: Path, monkeypatch):
    conf_dir = tmp_path / "etc" / "cloudx" / "compute-api"
    conf_dir.mkdir(parents=True)
    conf_file = conf_dir / "compute-api.conf"
    conf_file.write_text("""[DEFAULT]
host = 0.0.0.0
port = 9003

[database]
url = postgresql+psycopg2://cloudx:secret@127.0.0.1:5432/cloudx_db

[transport]
rabbitmq_url = amqp://cloudx:secret@127.0.0.1:5672/
""")

    monkeypatch.setattr("services.shared.config.ETC_CLOUDX_DIR", tmp_path / "etc" / "cloudx")

    # Clear env vars if set
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)

    config = load_service_config("compute-api", auto_env=True)

    assert config.get("host") == "0.0.0.0"
    assert config.get("port") == "9003"
    assert config.get("database_url") == "postgresql+psycopg2://cloudx:secret@127.0.0.1:5432/cloudx_db"
    assert config.get("rabbitmq_url") == "amqp://cloudx:secret@127.0.0.1:5672/"

    # Check os.environ auto injection
    assert os.getenv("DATABASE_URL") == "postgresql+psycopg2://cloudx:secret@127.0.0.1:5432/cloudx_db"
    assert os.getenv("RABBITMQ_URL") == "amqp://cloudx:secret@127.0.0.1:5672/"
    assert os.getenv("PORT") == "9003"

def test_load_service_config_fallback_to_env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("services.shared.config.ETC_CLOUDX_DIR", tmp_path / "non_existent")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PORT", "8003")

    config = load_service_config("compute-api", auto_env=True)

    assert config.get("database_url") == "sqlite:///:memory:"
    assert config.get("port") == "8003"


def test_shared_conf_is_overridden_by_service_conf(tmp_path: Path, monkeypatch):
    (tmp_path / "svc").mkdir()
    (tmp_path / "cloudx.conf").write_text("[DEFAULT]\nauth_api_url = http://shared:1\ninternal_token = a%b\n")
    (tmp_path / "svc" / "svc.conf").write_text("[DEFAULT]\nauth_api_url = http://svc:2\n")
    monkeypatch.setattr("services.shared.config.ETC_CLOUDX_DIR", tmp_path)
    for k in ("AUTH_API_URL", "INTERNAL_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    load_service_config("svc")
    assert os.environ["AUTH_API_URL"] == "http://svc:2"
    assert os.environ["INTERNAL_TOKEN"] == "a%b"
