import os
import configparser
from pathlib import Path
from typing import Dict, Any

ETC_CLOUDX_DIR = Path("/etc/cloudx")

def load_service_config(service_name: str, auto_env: bool = True) -> Dict[str, Any]:
    """
    Reads, in order (later wins):
      /etc/cloudx/cloudx.conf                       shared: secrets, service URLs, database, transport
      /etc/cloudx/<service_name>/<service_name>.conf   per-service overrides
    Keys are the lowercase names of the settings (auth_secret_key, auth_api_url, ...); injected into
    os.environ upper-cased when auto_env is True. Missing files fall back to existing environment variables.
    """
    config_data: Dict[str, Any] = {k.lower(): v for k, v in os.environ.items()}

    for conf_file in (ETC_CLOUDX_DIR / "cloudx.conf", ETC_CLOUDX_DIR / service_name / f"{service_name}.conf"):
        if not conf_file.exists():
            continue
        parser = configparser.ConfigParser(interpolation=None)  # URLs/passwords may contain '%'
        try:
            parser.read(conf_file)

            for k, v in parser.defaults().items():
                config_data[k.lower()] = v
                if auto_env:
                    os.environ[k.upper()] = v

            for section in parser.sections():
                for k, v in parser.items(section):
                    config_data[k.lower()] = v
                    config_data[f"{section}_{k}".lower()] = v
                    if auto_env:
                        os.environ[f"{section.upper()}_{k.upper()}"] = v
                        os.environ[k.upper()] = v
                    if section.lower() == "database" and k.lower() == "url":
                        config_data["database_url"] = v
                        if auto_env:
                            os.environ["DATABASE_URL"] = v
                    elif section.lower() == "transport" and k.lower() == "rabbitmq_url":
                        config_data["rabbitmq_url"] = v
                        if auto_env:
                            os.environ["RABBITMQ_URL"] = v
        except Exception as e:
            print(f"Warning: Failed to parse config file {conf_file}: {e}")

    if "database_url" not in config_data and "database_url" in os.environ:
        config_data["database_url"] = os.environ["DATABASE_URL"]
    if "rabbitmq_url" not in config_data and "rabbitmq_url" in os.environ:
        config_data["rabbitmq_url"] = os.environ["RABBITMQ_URL"]

    return config_data
