import os
import configparser
from pathlib import Path
from typing import Dict, Any

ETC_CLOUDX_DIR = Path("/etc/cloudx")

def load_service_config(service_name: str, auto_env: bool = True) -> Dict[str, Any]:
    """
    Reads configuration from /etc/cloudx/<service_name>/<service_name>.conf if present.
    Injects values into os.environ if auto_env is True.
    Falls back to existing environment variables if configuration file is absent.
    """
    config_data: Dict[str, Any] = {}
    
    # 1. Read existing environment variables as baseline fallback
    for k, v in os.environ.items():
        config_data[k.lower()] = v

    conf_file = ETC_CLOUDX_DIR / service_name / f"{service_name}.conf"
    if conf_file.exists():
        parser = configparser.ConfigParser()
        try:
            parser.read(conf_file)
            
            # Read [DEFAULT] section
            for k, v in parser.defaults().items():
                config_data[k.lower()] = v
                if auto_env:
                    os.environ[k.upper()] = v

            # Read all other sections
            for section in parser.sections():
                for k, v in parser.items(section):
                    key_lower = k.lower()
                    config_data[key_lower] = v
                    
                    # Map section specific keys to standard ENV names
                    section_prefix_key = f"{section.upper()}_{k.upper()}"
                    config_data[section_prefix_key.lower()] = v
                    if auto_env:
                        os.environ[section_prefix_key] = v
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

    # Ensure mapped keys exist in dictionary
    if "database_url" not in config_data and "database_url" in os.environ:
        config_data["database_url"] = os.environ["DATABASE_URL"]
    if "rabbitmq_url" not in config_data and "rabbitmq_url" in os.environ:
        config_data["rabbitmq_url"] = os.environ["RABBITMQ_URL"]

    return config_data
