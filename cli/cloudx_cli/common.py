import configparser
import json
import os
import sys
from pathlib import Path

import click
import requests
from tabulate import tabulate

#!/usr/bin/env python3
"""
cloudx — Unified CLI for cloudx cloud platform
================================================
Usage: cloudx [OPTIONS] COMMAND [ARGS]...

Authentication:
  Reads ~/.cloudx/credentials (INI format) for access key authentication.
  Configure with: cloudx auth configure
  Or set env vars: CLOUDX_ACCESS_KEY_ID + CLOUDX_SECRET_ACCESS_KEY

Examples:
  cloudx auth configure
  cloudx server list
  cloudx network create my-net
  cloudx volume create my-vol --size 20
"""
# ── Config helpers ─────────────────────────────────────────────────────────────
CLOUDX_DIR = Path.home() / ".cloudx"
CONFIG_FILE = CLOUDX_DIR / "config"
CREDS_FILE  = CLOUDX_DIR / "credentials"
DEFAULT_URLS = {
    "auth":          os.getenv("CLOUDX_AUTH_URL",          "http://127.0.0.1:8005"),
    "compute":       os.getenv("CLOUDX_COMPUTE_URL",       "http://127.0.0.1:8003"),
    "network":       os.getenv("CLOUDX_NETWORK_URL",       "http://127.0.0.1:8001"),
    "image":         os.getenv("CLOUDX_IMAGE_URL",         "http://127.0.0.1:8002"),
    "volume":        os.getenv("CLOUDX_VOLUME_URL",        "http://127.0.0.1:8004"),
    "placement":     os.getenv("CLOUDX_PLACEMENT_URL",     "http://127.0.0.1:8006"),
    "object-store":  os.getenv("CLOUDX_OBJECT_STORE_URL",  "http://127.0.0.1:8007"),
    "load-balancer": os.getenv("CLOUDX_LOAD_BALANCER_URL", "http://127.0.0.1:8008"),
    "telemetry":     os.getenv("CLOUDX_TELEMETRY_URL",     "http://127.0.0.1:8010"),
    "billing":       os.getenv("CLOUDX_BILLING_URL",       "http://127.0.0.1:8011"),
    "event":         os.getenv("CLOUDX_EVENT_URL",         "http://127.0.0.1:8012"),
    "orchestration": os.getenv("CLOUDX_ORCHESTRATION_URL", "http://127.0.0.1:8013"),
    "key-manager":   os.getenv("CLOUDX_KEY_MANAGER_URL",   "http://127.0.0.1:8014"),
    "k8s-engine":   os.getenv("CLOUDX_K8S_ENGINE_URL",   "http://127.0.0.1:8015"),
}


def _load_config(profile: str = "default") -> dict:
    cfg = {}
    if CONFIG_FILE.exists():
        parser = configparser.ConfigParser()
        parser.read(CONFIG_FILE)
        # [DEFAULT] is read via parser.defaults() — has_section("default") is False
        cfg.update(dict(parser.defaults()))
        section = f"profile {profile}" if profile != "default" else None
        if section and parser.has_section(section):
            cfg.update(dict(parser[section]))
    return cfg


def _load_creds(profile: str = "default") -> dict:
    creds = {}
    if CREDS_FILE.exists():
        parser = configparser.ConfigParser()
        parser.read(CREDS_FILE)
        # configparser treats [DEFAULT] as special fallback values, not a named
        # section — parser.has_section("default") returns False for [DEFAULT].
        # Use parser.defaults() to always read DEFAULT values first, then
        # override with the named profile section if it exists.
        creds.update(dict(parser.defaults()))          # reads [DEFAULT]
        if parser.has_section(profile):                # named [profile]
            creds.update(dict(parser[profile]))
    return creds


def _get_token(profile: str = "default") -> str | None:
    """Obtain a JWT token via access key exchange.

    Sources (checked in order):
      1. CLOUDX_TOKEN env var (pre-existing token)
      2. Access key exchange: env vars or ~/.cloudx/credentials
    """
    # 1. Env var (highest priority)
    if t := os.getenv("CLOUDX_TOKEN"):
        return t

    # 2. Direct username + password login
    username = os.getenv("CLOUDX_USERNAME")
    password = os.getenv("CLOUDX_PASSWORD")
    if username and password:
        try:
            r = requests.post(
                f"{DEFAULT_URLS['auth']}/auth/tokens",
                json={"username": username, "password": password},
                timeout=5,
            )
            if r.status_code == 201:
                return r.json()["token"]
            click.echo(f"❌ Username/password authentication failed (HTTP {r.status_code})", err=True)
            sys.exit(1)
        except requests.ConnectionError:
            click.echo(f"❌ Cannot connect to auth-api at {DEFAULT_URLS['auth']}", err=True)
            sys.exit(1)
        except Exception as e:
            click.echo(f"❌ Authentication error: {e}", err=True)
            sys.exit(1)

    # 3. Access key exchange
    creds = _load_creds(profile)
    key_id = os.getenv("CLOUDX_ACCESS_KEY_ID") or creds.get("access_key_id", "")
    secret = os.getenv("CLOUDX_SECRET_ACCESS_KEY") or creds.get("secret_access_key", "")
    if key_id and secret:
        try:
            r = requests.post(
                f"{DEFAULT_URLS['auth']}/auth/tokens/access-key",
                headers={"X-Access-Key-Id": key_id, "X-Secret-Access-Key": secret},
                timeout=5,
            )
            if r.status_code == 201:
                return r.json()["token"]
            click.echo(f"❌ Access key authentication failed (HTTP {r.status_code})", err=True)
            sys.exit(1)
        except requests.ConnectionError:
            click.echo(f"❌ Cannot connect to auth-api at {DEFAULT_URLS['auth']}", err=True)
            sys.exit(1)
        except Exception as e:
            click.echo(f"❌ Access key authentication error: {e}", err=True)
            sys.exit(1)
    return None


def _headers(profile: str = "default") -> dict:
    token = _get_token(profile)
    if not token:
        click.echo("❌ Not authenticated. Run: cloudx auth configure", err=True)
        click.echo(f"   (credentials file: {CREDS_FILE})", err=True)
        sys.exit(1)
    return {"X-Auth-Token": token, "Content-Type": "application/json"}


def _fmt_value(v) -> str:
    """Format a value for table display (handle dicts/lists cleanly)."""
    if v is None:
        return "None"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, list):
        if not v:
            return ""
        # If list of strings/numbers → comma-separated
        if all(isinstance(i, (str, int, float)) for i in v):
            return ", ".join(str(i) for i in v)
        # Otherwise JSON each item on its own line
        return "\n".join(json.dumps(i, default=str) for i in v)
    if isinstance(v, dict):
        # Format as multiline key=value
        return "\n".join(f"{k}='{val}'" for k, val in v.items())
    return str(v)


def _output(data, fmt: str, headers: list | None = None):
    """Print data as json or table (formatted table grid)."""
    if fmt == "json":
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        if isinstance(data, list):
            if not data:
                click.echo("(empty)")
                return
            if headers is None:
                headers = list(data[0].keys()) if data else []
            rows = [[_fmt_value(item.get(h, "")) for h in headers] for item in data]
            click.echo(tabulate(rows, headers=headers, tablefmt="grid"))
        elif isinstance(data, dict):
            rows = [[k, _fmt_value(v)] for k, v in data.items()]
            click.echo(tabulate(rows, headers=["Field", "Value"], tablefmt="grid"))
        else:
            click.echo(str(data))


def _api(method: str, url: str, profile: str = "default", **kwargs):
    h = _headers(profile)
    h.update(kwargs.pop("headers", {}))
    try:
        r = getattr(requests, method)(url, headers=h, timeout=30, **kwargs)
        if r.status_code == 204:
            return None
        if not r.ok:
            try:
                err = r.json()
            except ValueError:
                err = {"detail": r.text}
            click.echo(f"❌ HTTP {r.status_code}: {err.get('detail', err)}", err=True)
            sys.exit(1)
        return r.json()
    except requests.ConnectionError:
        click.echo(f"❌ Cannot connect to {url.split('/')[2]}", err=True)
        sys.exit(1)
