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
import configparser
import json
import os
import sys
from pathlib import Path

import click
import requests
from tabulate import tabulate

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
    # 2. Access key exchange
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
    """Print data as json or table (OpenStack-style grid)."""
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
            except Exception:
                err = {"detail": r.text}
            click.echo(f"❌ HTTP {r.status_code}: {err.get('detail', err)}", err=True)
            sys.exit(1)
        return r.json()
    except requests.ConnectionError:
        click.echo(f"❌ Cannot connect to {url.split('/')[2]}", err=True)
        sys.exit(1)


# ── Main group ─────────────────────────────────────────────────────────────────
CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("--profile", "-p", default="default", help="Config profile name")
@click.option("--output", "-o", "fmt", default=None,
              type=click.Choice(["json", "table"]), help="Output format")
@click.pass_context
def cli(ctx, profile, fmt):
    """☁  cloudx — Cloud management CLI"""
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile
    # Determine output format
    if fmt is None:
        cfg = _load_config(profile)
        fmt = cfg.get("output", "table")
    ctx.obj["fmt"] = fmt


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def auth(ctx):
    """Authentication and identity management"""


@auth.command("configure")
@click.pass_context
def auth_configure(ctx):
    """Configure access key credentials and CLI preferences"""
    click.echo("☁  cloudx configure\n")
    CLOUDX_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing values as defaults
    existing_creds = _load_creds("default")
    existing_config = _load_config("default")

    access_key_id = click.prompt(
        "CloudX Access Key ID",
        default=existing_creds.get("access_key_id", "") or None,
    )
    secret_key = click.prompt(
        "CloudX Secret Access Key",
        default=existing_creds.get("secret_access_key", "") or None,
        hide_input=True,
    )
    project = click.prompt(
        "Default project",
        default=existing_config.get("project", "admin"),
    )
    fmt = click.prompt(
        "Default output format",
        type=click.Choice(["json", "table"]),
        default=existing_config.get("output", "table"),
    )

    # Write config
    cfg = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE)
    if not cfg.has_section("default"):
        cfg["DEFAULT"] = {}
    cfg["DEFAULT"]["project"] = project
    cfg["DEFAULT"]["output"]  = fmt
    with open(CONFIG_FILE, "w") as f:
        cfg.write(f)
    CONFIG_FILE.chmod(0o600)

    # Write credentials
    crd = configparser.ConfigParser()
    if CREDS_FILE.exists():
        crd.read(CREDS_FILE)
    if not crd.has_section("default"):
        crd["DEFAULT"] = {}
    crd["DEFAULT"]["access_key_id"]     = access_key_id
    crd["DEFAULT"]["secret_access_key"] = secret_key
    with open(CREDS_FILE, "w") as f:
        crd.write(f)
    CREDS_FILE.chmod(0o600)

    # Validate credentials
    click.echo("\nValidating credentials...", nl=False)
    try:
        r = requests.post(
            f"{DEFAULT_URLS['auth']}/auth/tokens/access-key",
            headers={"X-Access-Key-Id": access_key_id, "X-Secret-Access-Key": secret_key},
            timeout=5,
        )
        if r.status_code == 201:
            click.echo(" ✓")
        else:
            click.echo(f" ✗ (HTTP {r.status_code})")
            click.echo("⚠️  Credentials saved but validation failed. Check your access key.", err=True)
    except requests.ConnectionError:
        click.echo(f" ✗ (connection failed)")
        click.echo(f"⚠️  Credentials saved but could not reach auth-api at {DEFAULT_URLS['auth']}", err=True)

    click.echo(f"\n✓ Configuration saved to {CLOUDX_DIR}")


# Project subcommands
@auth.group("project")
def auth_project():
    """Manage projects"""

@auth_project.command("create")
@click.argument("name")
@click.option("--description", "-d", default="")
@click.pass_context
def project_create(ctx, name, description):
    data = _api("post", f"{DEFAULT_URLS['auth']}/projects",
                ctx.obj["profile"], json={"name": name, "description": description})
    _output(data, ctx.obj["fmt"])

@auth_project.command("list")
@click.pass_context
def project_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['auth']}/projects", ctx.obj["profile"])
    _output(data["items"], ctx.obj["fmt"], ["id", "name", "description", "enabled"])

@auth_project.command("show")
@click.argument("ref")
@click.pass_context
def project_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['auth']}/projects/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@auth_project.command("delete")
@click.argument("ref")
@click.pass_context
def project_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['auth']}/projects/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Project {ref} deleted")


# User subcommands
@auth.group("user")
def auth_user():
    """Manage users"""

@auth_user.command("create")
@click.argument("username")
@click.option("--email", required=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@click.option("--project", required=True)
@click.option("--role", type=click.Choice(["admin", "member"]), default="member")
@click.pass_context
def user_create(ctx, username, email, password, project, role):
    data = _api("post", f"{DEFAULT_URLS['auth']}/users", ctx.obj["profile"],
                json={"username": username, "email": email, "password": password,
                      "project_id": project, "role": role})
    _output(data, ctx.obj["fmt"])

@auth_user.command("list")
@click.pass_context
def user_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['auth']}/users", ctx.obj["profile"])
    _output(data["items"], ctx.obj["fmt"], ["id", "username", "email", "role", "enabled"])

@auth_user.command("show")
@click.argument("ref")
@click.pass_context
def user_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['auth']}/users/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@auth_user.command("set")
@click.argument("ref")
@click.option("--role", type=click.Choice(["admin", "member"]))
@click.option("--email")
@click.option("--password")
@click.option("--enable/--disable", default=None)
@click.pass_context
def user_set(ctx, ref, role, email, password, enable):
    payload = {}
    if role:     payload["role"] = role
    if email:    payload["email"] = email
    if password: payload["password"] = password
    if enable is not None: payload["enabled"] = enable
    data = _api("put", f"{DEFAULT_URLS['auth']}/users/{ref}", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@auth_user.command("delete")
@click.argument("ref")
@click.pass_context
def user_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['auth']}/users/{ref}", ctx.obj["profile"])
    click.echo(f"✓ User {ref} deleted")


# Group subcommands
@auth.group("group")
def auth_group():
    """Manage groups"""

@auth_group.command("create")
@click.argument("name")
@click.option("--description", "-d", default="")
@click.pass_context
def group_create(ctx, name, description):
    data = _api("post", f"{DEFAULT_URLS['auth']}/groups", ctx.obj["profile"],
                json={"name": name, "description": description})
    _output(data, ctx.obj["fmt"])

@auth_group.command("list")
@click.pass_context
def group_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['auth']}/groups", ctx.obj["profile"])
    _output(data["items"], ctx.obj["fmt"], ["id", "name", "project_id"])

@auth_group.command("add-member")
@click.argument("group")
@click.argument("user")
@click.pass_context
def group_add_member(ctx, group, user):
    _api("put", f"{DEFAULT_URLS['auth']}/groups/{group}/members/{user}", ctx.obj["profile"])
    click.echo(f"✓ User {user} added to group {group}")

@auth_group.command("remove-member")
@click.argument("group")
@click.argument("user")
@click.pass_context
def group_remove_member(ctx, group, user):
    _api("delete", f"{DEFAULT_URLS['auth']}/groups/{group}/members/{user}", ctx.obj["profile"])
    click.echo(f"✓ User {user} removed from group {group}")

@auth_group.command("delete")
@click.argument("ref")
@click.pass_context
def group_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['auth']}/groups/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Group {ref} deleted")


# Access key subcommands
@auth.group("access-key")
def auth_access_key():
    """Manage access keys (CLI credentials)"""

@auth_access_key.command("create")
@click.option("--name", default="default")
@click.pass_context
def access_key_create(ctx, name):
    """Generate a new access key. Secret is shown ONCE."""
    data = _api("post", f"{DEFAULT_URLS['auth']}/access-keys", ctx.obj["profile"],
                json={"name": name})
    click.echo(f"\n✓ Access key created. Save the secret now — it won't be shown again!\n")
    click.echo(f"  Access Key ID:     {data['access_key_id']}")
    click.echo(f"  Secret Access Key: {data.get('secret_access_key', '(hidden)')}\n")
    click.echo(f"Add to ~/.cloudx/credentials:")
    click.echo(f"  [default]")
    click.echo(f"  access_key_id = {data['access_key_id']}")
    click.echo(f"  secret_access_key = {data.get('secret_access_key', '...')}")

@auth_access_key.command("list")
@click.pass_context
def access_key_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['auth']}/access-keys", ctx.obj["profile"])
    _output(data["items"], ctx.obj["fmt"], ["id", "access_key_id", "name", "enabled", "created_at"])

@auth_access_key.command("revoke")
@click.argument("key_id")
@click.pass_context
def access_key_revoke(ctx, key_id):
    _api("delete", f"{DEFAULT_URLS['auth']}/access-keys/{key_id}", ctx.obj["profile"])
    click.echo(f"✓ Access key {key_id} revoked")


# ═══════════════════════════════════════════════════════════════════════════════
# SERVER (Compute)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def server(ctx):
    """Manage compute instances"""

@server.command("create")
@click.argument("name")
@click.option("--image", required=True)
@click.option("--flavor", default="tiny")
@click.option("--network", required=True)
@click.option("--key-name")
@click.option("--security-group", "security_groups", multiple=True)
@click.option("--user-data", type=click.Path())
@click.option("--volume-size", type=int, help="Root volume size GB (boot from volume)")
@click.pass_context
def server_create(ctx, name, image, flavor, network, key_name, security_groups, user_data, volume_size):
    payload = {"name": name, "image": image, "flavor": flavor, "network": network}
    if key_name:          payload["key_name"] = key_name
    if security_groups:   payload["security_groups"] = list(security_groups)
    if volume_size:       payload["volume_size"] = volume_size
    if user_data:
        with open(user_data) as f:
            payload["user_data"] = f.read()
    data = _api("post", f"{DEFAULT_URLS['compute']}/servers", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@server.command("list")
@click.pass_context
def server_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"],
            ["id", "name", "status", "flavor", "ip_address", "floating_ip"])

@server.command("show")
@click.argument("ref")
@click.pass_context
def server_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@server.command("delete")
@click.argument("ref")
@click.pass_context
def server_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['compute']}/servers/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} deleted")

@server.command("start")
@click.argument("ref")
@click.pass_context
def server_start(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/start", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} started")

@server.command("stop")
@click.argument("ref")
@click.pass_context
def server_stop(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/stop", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} stopped")

@server.command("reboot")
@click.argument("ref")
@click.option("--hard", is_flag=True)
@click.pass_context
def server_reboot(ctx, ref, hard):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/reboot",
         ctx.obj["profile"], json={"type": "hard" if hard else "soft"})
    click.echo(f"✓ Server {ref} rebooted")

@server.command("pause")
@click.argument("ref")
@click.pass_context
def server_pause(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/pause", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} paused")

@server.command("resume")
@click.argument("ref")
@click.pass_context
def server_resume(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/resume", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} resumed")

@server.command("suspend")
@click.argument("ref")
@click.pass_context
def server_suspend(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/suspend", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} suspended")

@server.command("resize")
@click.argument("ref")
@click.option("--flavor", required=True)
@click.pass_context
def server_resize(ctx, ref, flavor):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/resize",
         ctx.obj["profile"], json={"flavor": flavor})
    click.echo(f"✓ Server {ref} resized to {flavor}")

@server.command("rebuild")
@click.argument("ref")
@click.option("--image", required=True)
@click.pass_context
def server_rebuild(ctx, ref, image):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/rebuild",
         ctx.obj["profile"], json={"image": image})
    click.echo(f"✓ Server {ref} rebuilding with image {image}")

@server.command("rescue")
@click.argument("ref")
@click.option("--image", help="Rescue image (defaults to server's original image)")
@click.pass_context
def server_rescue(ctx, ref, image):
    payload = {}
    if image: payload["image"] = image
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/rescue",
         ctx.obj["profile"], json=payload)
    click.echo(f"✓ Server {ref} in rescue mode")

@server.command("unrescue")
@click.argument("ref")
@click.pass_context
def server_unrescue(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/unrescue", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} unrescued")

@server.command("console")
@click.argument("ref")
@click.pass_context
def server_console(ctx, ref):
    """Open serial console (Ctrl+] to exit)"""
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}/console", ctx.obj["profile"])
    import shutil
    import subprocess
    virsh_uri = "qemu:///system"
    domain = data.get("domain_name", ref)
    if shutil.which("virsh") is None:
        click.echo(f"❌ 'virsh' is not installed on this host.", err=True)
        click.echo(f"To access the console, either install libvirt-clients, or run from the compute-worker container:", err=True)
        click.echo(f"  docker exec -it mini-openstack-lab-compute-worker-1 virsh -c qemu:///system console {domain}", err=True)
        sys.exit(1)
    os.execvp("virsh", ["virsh", "-c", virsh_uri, "console", domain])

@server.command("console-url")
@click.argument("ref")
@click.pass_context
def server_console_url(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}/console-url", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@server.command("logs")
@click.argument("ref")
@click.option("--lines", "-n", default=50)
@click.pass_context
def server_logs(ctx, ref, lines):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}/logs?lines={lines}", ctx.obj["profile"])
    click.echo(data.get("output", ""))

@server.command("ssh-info")
@click.argument("ref")
@click.pass_context
def server_ssh_info(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}/ssh-info", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@server.command("metadata")
@click.argument("ref")
@click.pass_context
def server_metadata(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}/metadata", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@server.command("set-password")
@click.argument("ref")
@click.option("--password", prompt=True, hide_input=True)
@click.pass_context
def server_set_password(ctx, ref, password):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/password",
         ctx.obj["profile"], json={"password": password})
    click.echo(f"✓ Password updated for {ref}")

@server.command("snapshot")
@click.argument("ref")
@click.option("--name", required=True)
@click.pass_context
def server_snapshot(ctx, ref, name):
    data = _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/snapshots",
                ctx.obj["profile"], json={"name": name})
    _output(data, ctx.obj["fmt"])

@server.group("port")
def server_port():
    """Manage server network ports"""

@server_port.command("add")
@click.argument("server_ref")
@click.option("--network", required=True)
@click.pass_context
def server_port_add(ctx, server_ref, network):
    data = _api("post", f"{DEFAULT_URLS['compute']}/servers/{server_ref}/ports",
                ctx.obj["profile"], json={"network": network})
    _output(data, ctx.obj["fmt"])

@server_port.command("remove")
@click.argument("server_ref")
@click.argument("port_ref")
@click.pass_context
def server_port_remove(ctx, server_ref, port_ref):
    _api("delete", f"{DEFAULT_URLS['compute']}/servers/{server_ref}/ports/{port_ref}",
         ctx.obj["profile"])
    click.echo(f"✓ Port {port_ref} detached")

@server.group("volume")
def server_volume():
    """Manage server volumes"""

@server_volume.command("attach")
@click.argument("server_ref")
@click.option("--volume", required=True)
@click.pass_context
def server_volume_attach(ctx, server_ref, volume):
    data = _api("post", f"{DEFAULT_URLS['compute']}/servers/{server_ref}/volumes/attach",
                ctx.obj["profile"], json={"volume_id": volume})
    _output(data, ctx.obj["fmt"])

@server_volume.command("detach")
@click.argument("server_ref")
@click.option("--volume", required=True)
@click.pass_context
def server_volume_detach(ctx, server_ref, volume):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{server_ref}/volumes/detach",
         ctx.obj["profile"], json={"volume_id": volume})
    click.echo(f"✓ Volume {volume} detached")


# ═══════════════════════════════════════════════════════════════════════════════
# FLAVOR
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def flavor(ctx):
    """Manage compute flavors"""

@flavor.command("list")
@click.pass_context
def flavor_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/flavors", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"], ["id", "name", "vcpus", "ram", "disk"])

@flavor.command("show")
@click.argument("ref")
@click.pass_context
def flavor_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/flavors/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@flavor.command("create")
@click.argument("name")
@click.option("--vcpus", type=int, required=True)
@click.option("--ram", type=int, required=True, help="RAM in MB")
@click.option("--disk", type=int, required=True, help="Disk in GB")
@click.pass_context
def flavor_create(ctx, name, vcpus, ram, disk):
    data = _api("post", f"{DEFAULT_URLS['compute']}/flavors", ctx.obj["profile"],
                json={"name": name, "vcpus": vcpus, "ram": ram, "disk": disk})
    _output(data, ctx.obj["fmt"])

@flavor.command("delete")
@click.argument("ref")
@click.pass_context
def flavor_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['compute']}/flavors/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Flavor {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# KEYPAIR
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def keypair(ctx):
    """Manage SSH keypairs"""

@keypair.command("create")
@click.argument("name")
@click.pass_context
def keypair_create(ctx, name):
    data = _api("post", f"{DEFAULT_URLS['compute']}/keypairs", ctx.obj["profile"],
                json={"name": name})
    if "private_key" in data:
        fname = f"{name}.pem"
        with open(fname, "w") as f:
            f.write(data["private_key"])
        os.chmod(fname, 0o600)
        click.echo(f"✓ Keypair {name} created. Private key saved to {fname}")
    else:
        _output(data, ctx.obj["fmt"])

@keypair.command("import")
@click.argument("name")
@click.option("--public-key-file", type=click.Path(exists=True), required=True)
@click.pass_context
def keypair_import(ctx, name, public_key_file):
    with open(public_key_file) as f:
        pub_key = f.read().strip()
    data = _api("post", f"{DEFAULT_URLS['compute']}/keypairs/import", ctx.obj["profile"],
                json={"name": name, "public_key": pub_key})
    _output(data, ctx.obj["fmt"])

@keypair.command("list")
@click.pass_context
def keypair_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/keypairs", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"], ["name", "fingerprint", "created_at"])

@keypair.command("show")
@click.argument("name")
@click.pass_context
def keypair_show(ctx, name):
    data = _api("get", f"{DEFAULT_URLS['compute']}/keypairs/{name}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@keypair.command("delete")
@click.argument("name")
@click.pass_context
def keypair_delete(ctx, name):
    _api("delete", f"{DEFAULT_URLS['compute']}/keypairs/{name}", ctx.obj["profile"])
    click.echo(f"✓ Keypair {name} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# SERVER GROUP
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("server-group")
@click.pass_context
def server_group(ctx):
    """Manage server groups (anti-affinity)"""

@server_group.command("create")
@click.argument("name")
@click.option("--policy", type=click.Choice(["anti-affinity", "affinity", "soft-anti-affinity"]), default="anti-affinity")
@click.pass_context
def sg_create(ctx, name, policy):
    data = _api("post", f"{DEFAULT_URLS['compute']}/server-groups", ctx.obj["profile"],
                json={"name": name, "policy": policy})
    _output(data, ctx.obj["fmt"])

@server_group.command("list")
@click.pass_context
def sg_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/server-groups", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "policy", "members"])

@server_group.command("show")
@click.argument("ref")
@click.pass_context
def sg_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/server-groups/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@server_group.command("delete")
@click.argument("ref")
@click.pass_context
def sg_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['compute']}/server-groups/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Server group {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# NETWORK
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def network(ctx):
    """Manage networks"""

@network.command("create")
@click.argument("name")
@click.option("--external", is_flag=True)
@click.option("--shared", is_flag=True)
@click.pass_context
def network_create(ctx, name, external, shared):
    data = _api("post", f"{DEFAULT_URLS['network']}/networks", ctx.obj["profile"],
                json={"name": name, "external": external, "shared": shared})
    _output(data, ctx.obj["fmt"])

@network.command("list")
@click.pass_context
def network_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/networks", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "status", "router:external", "admin_state_up", "mtu"])

@network.command("show")
@click.argument("ref")
@click.pass_context
def network_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/networks/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@network.command("delete")
@click.argument("ref")
@click.pass_context
def network_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['network']}/networks/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Network {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# SUBNET
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def subnet(ctx):
    """Manage subnets"""

@subnet.command("create")
@click.argument("name")
@click.option("--network", required=True)
@click.option("--cidr", required=True)
@click.option("--gateway")
@click.option("--ip-version", type=click.Choice(["4", "6"]), default="4")
@click.option("--dhcp/--no-dhcp", default=True)
@click.option("--dns-nameserver", "dns", multiple=True)
@click.option("--allocation-start")
@click.option("--allocation-end")
@click.pass_context
def subnet_create(ctx, name, network, cidr, gateway, ip_version, dhcp, dns, allocation_start, allocation_end):
    payload = {"name": name, "network": network, "cidr": cidr, "enable_dhcp": dhcp,
               "ip_version": int(ip_version)}
    if gateway:           payload["gateway"] = gateway
    if dns:               payload["dns_nameservers"] = ",".join(dns)
    if allocation_start:  payload["allocation_start"] = allocation_start
    if allocation_end:    payload["allocation_end"] = allocation_end
    data = _api("post", f"{DEFAULT_URLS['network']}/subnets", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@subnet.command("list")
@click.pass_context
def subnet_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/subnets", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "cidr", "gateway_ip", "network_id", "ip_version"])

@subnet.command("show")
@click.argument("ref")
@click.pass_context
def subnet_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/subnets/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@subnet.command("delete")
@click.argument("ref")
@click.pass_context
def subnet_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['network']}/subnets/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Subnet {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTER
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def router(ctx):
    """Manage routers"""

@router.command("create")
@click.argument("name")
@click.pass_context
def router_create(ctx, name):
    data = _api("post", f"{DEFAULT_URLS['network']}/routers", ctx.obj["profile"],
                json={"name": name})
    _output(data, ctx.obj["fmt"])

@router.command("list")
@click.pass_context
def router_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/routers", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "status", "admin_state_up"])

@router.command("show")
@click.argument("ref")
@click.pass_context
def router_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/routers/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@router.command("delete")
@click.argument("ref")
@click.pass_context
def router_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['network']}/routers/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Router {ref} deleted")

@router.command("set-gateway")
@click.argument("ref")
@click.argument("network")
@click.pass_context
def router_set_gateway(ctx, ref, network):
    _api("post", f"{DEFAULT_URLS['network']}/routers/{ref}/gateway",
         ctx.obj["profile"], json={"external_network": network})
    click.echo(f"✓ Gateway set on router {ref}")

@router.command("add-interface")
@click.argument("ref")
@click.option("--subnet", required=True)
@click.pass_context
def router_add_interface(ctx, ref, subnet):
    _api("post", f"{DEFAULT_URLS['network']}/routers/{ref}/interfaces",
         ctx.obj["profile"], json={"subnet": subnet})
    click.echo(f"✓ Interface added to router {ref}")

@router.command("remove-interface")
@click.argument("ref")
@click.argument("subnet")
@click.pass_context
def router_remove_interface(ctx, ref, subnet):
    _api("delete", f"{DEFAULT_URLS['network']}/routers/{ref}/interfaces/{subnet}",
         ctx.obj["profile"])
    click.echo(f"✓ Interface removed from router {ref}")


# ═══════════════════════════════════════════════════════════════════════════════
# PORT
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def port(ctx):
    """Manage network ports"""

@port.command("list")
@click.pass_context
def port_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/ports", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "fixed_ip", "mac_address", "status"])

@port.command("show")
@click.argument("ref")
@click.pass_context
def port_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/ports/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@port.command("set-security-group")
@click.argument("ref")
@click.option("--security-group", "sgs", multiple=True, required=True)
@click.pass_context
def port_set_sg(ctx, ref, sgs):
    _api("post", f"{DEFAULT_URLS['network']}/ports/{ref}/security-groups",
         ctx.obj["profile"], json={"security_groups": list(sgs)})
    click.echo(f"✓ Security groups updated on port {ref}")

@port.command("clear-security-groups")
@click.argument("ref")
@click.pass_context
def port_clear_sg(ctx, ref):
    _api("post", f"{DEFAULT_URLS['network']}/ports/{ref}/security-groups/clear",
         ctx.obj["profile"])
    click.echo(f"✓ Security groups cleared on port {ref}")


# ═══════════════════════════════════════════════════════════════════════════════
# FLOATING IP
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("floating-ip")
@click.pass_context
def floating_ip(ctx):
    """Manage floating IPs"""

@floating_ip.command("create")
@click.argument("network")
@click.pass_context
def fip_create(ctx, network):
    data = _api("post", f"{DEFAULT_URLS['network']}/floating-ips", ctx.obj["profile"],
                json={"network": network})
    _output(data, ctx.obj["fmt"])

@floating_ip.command("list")
@click.pass_context
def fip_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/floating-ips", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "floating_ip_address", "fixed_ip_address", "status", "router_id"])

@floating_ip.command("associate")
@click.argument("ip")
@click.option("--server", required=True)
@click.option("--router", required=True)
@click.pass_context
def fip_associate(ctx, ip, server, router):
    port = _api("get", f"{DEFAULT_URLS['compute']}/servers/{server}",
                ctx.obj["profile"]).get("port_id")
    if not port:
        click.echo(f"❌ No port found for server '{server}'", err=True)
        sys.exit(1)
    _api("post", f"{DEFAULT_URLS['network']}/floating-ips/{ip}/associate",
         ctx.obj["profile"], json={"server": server, "port": port, "router": router})
    click.echo(f"✓ Floating IP {ip} associated")

@floating_ip.command("disassociate")
@click.argument("ip")
@click.pass_context
def fip_disassociate(ctx, ip):
    _api("post", f"{DEFAULT_URLS['network']}/floating-ips/{ip}/disassociate",
         ctx.obj["profile"])
    click.echo(f"✓ Floating IP {ip} disassociated")

@floating_ip.command("show")
@click.argument("ip")
@click.pass_context
def fip_show(ctx, ip):
    data = _api("get", f"{DEFAULT_URLS['network']}/floating-ips/{ip}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@floating_ip.command("delete")
@click.argument("ip")
@click.pass_context
def fip_delete(ctx, ip):
    _api("delete", f"{DEFAULT_URLS['network']}/floating-ips/{ip}", ctx.obj["profile"])
    click.echo(f"✓ Floating IP {ip} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# SECURITY GROUP
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("security-group")
@click.pass_context
def security_group(ctx):
    """Manage security groups"""

@security_group.command("create")
@click.argument("name")
@click.option("--description", "-d", default="")
@click.pass_context
def sg_create(ctx, name, description):
    data = _api("post", f"{DEFAULT_URLS['network']}/security-groups", ctx.obj["profile"],
                json={"name": name, "description": description})
    _output(data, ctx.obj["fmt"])

@security_group.command("list")
@click.pass_context
def sg_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/security-groups", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "description"])

@security_group.command("show")
@click.argument("ref")
@click.pass_context
def sg_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/security-groups/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@security_group.command("delete")
@click.argument("ref")
@click.pass_context
def sg_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['network']}/security-groups/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Security group {ref} deleted")

@security_group.group("rule")
def sg_rule():
    """Manage security group rules"""

@sg_rule.command("add")
@click.argument("sg_ref")
@click.option("--direction", type=click.Choice(["ingress", "egress"]), default="ingress")
@click.option("--protocol", type=click.Choice(["tcp", "udp", "icmp", "any"]), default="tcp")
@click.option("--port", type=int)
@click.option("--port-range-min", type=int)
@click.option("--port-range-max", type=int)
@click.option("--remote-ip", default="0.0.0.0/0")
@click.pass_context
def sg_rule_add(ctx, sg_ref, direction, protocol, port, port_range_min, port_range_max, remote_ip):
    payload = {"direction": direction, "protocol": protocol, "remote_ip_prefix": remote_ip}
    if port:             payload["port_range_min"] = payload["port_range_max"] = port
    if port_range_min:   payload["port_range_min"] = port_range_min
    if port_range_max:   payload["port_range_max"] = port_range_max
    data = _api("post", f"{DEFAULT_URLS['network']}/security-groups/{sg_ref}/rules",
                ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@sg_rule.command("list")
@click.argument("sg_ref")
@click.pass_context
def sg_rule_list(ctx, sg_ref):
    data = _api("get", f"{DEFAULT_URLS['network']}/security-groups/{sg_ref}/rules", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"],
            ["id", "direction", "protocol", "port_range_min", "port_range_max", "remote_ip_prefix"])

@sg_rule.command("delete")
@click.argument("rule_id")
@click.pass_context
def sg_rule_delete(ctx, rule_id):
    _api("delete", f"{DEFAULT_URLS['network']}/security-group-rules/{rule_id}", ctx.obj["profile"])
    click.echo(f"✓ Rule {rule_id} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def image(ctx):
    """Manage images"""

@image.command("list")
@click.option("--visibility", type=click.Choice(["public", "private", "shared", "community"]))
@click.option("--status")
@click.option("--limit", type=int, default=50)
@click.pass_context
def image_list(ctx, visibility, status, limit):
    params = f"?limit={limit}"
    if visibility: params += f"&visibility={visibility}"
    if status:     params += f"&status={status}"
    data = _api("get", f"{DEFAULT_URLS['image']}/images{params}", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"],
            ["id", "name", "status", "visibility", "disk_format", "size"])

@image.command("show")
@click.argument("ref")
@click.pass_context
def image_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['image']}/images/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@image.command("upload")
@click.argument("name")
@click.option("--file", "filepath", type=click.Path(exists=True), required=True)
@click.option("--disk-format", default="qcow2", type=click.Choice(["qcow2", "raw", "vmdk"]))
@click.option("--visibility", default="public", type=click.Choice(["public", "private", "shared"]))
@click.pass_context
def image_upload(ctx, name, filepath, disk_format, visibility):
    token = _get_token(ctx.obj["profile"])
    if not token:
        click.echo("❌ Not authenticated", err=True); sys.exit(1)
    with open(filepath, "rb") as f:
        r = requests.post(
            f"{DEFAULT_URLS['image']}/images",
            headers={"X-Auth-Token": token},
            data={"name": name, "disk_format": disk_format, "visibility": visibility},
            files={"file": f},
            timeout=300,
        )
    if not r.ok:
        click.echo(f"❌ Upload failed: {r.text}", err=True); sys.exit(1)
    _output(r.json(), ctx.obj["fmt"])

@image.command("delete")
@click.argument("ref")
@click.pass_context
def image_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['image']}/images/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Image {ref} deleted")

@image.command("deactivate")
@click.argument("ref")
@click.pass_context
def image_deactivate(ctx, ref):
    _api("post", f"{DEFAULT_URLS['image']}/images/{ref}/deactivate", ctx.obj["profile"])
    click.echo(f"✓ Image {ref} deactivated")

@image.command("reactivate")
@click.argument("ref")
@click.pass_context
def image_reactivate(ctx, ref):
    _api("post", f"{DEFAULT_URLS['image']}/images/{ref}/reactivate", ctx.obj["profile"])
    click.echo(f"✓ Image {ref} reactivated")

@image.command("set-visibility")
@click.argument("ref")
@click.argument("visibility", type=click.Choice(["public", "private", "shared", "community"]))
@click.pass_context
def image_set_visibility(ctx, ref, visibility):
    _api("patch", f"{DEFAULT_URLS['image']}/images/{ref}",
         ctx.obj["profile"], json={"visibility": visibility})
    click.echo(f"✓ Image {ref} visibility set to {visibility}")

@image.group("member")
def image_member():
    """Manage image sharing"""

@image_member.command("add")
@click.argument("image_ref")
@click.argument("project_id")
@click.pass_context
def image_member_add(ctx, image_ref, project_id):
    _api("post", f"{DEFAULT_URLS['image']}/images/{image_ref}/members",
         ctx.obj["profile"], json={"project_id": project_id})
    click.echo(f"✓ Image shared with project {project_id}")

@image_member.command("list")
@click.argument("image_ref")
@click.pass_context
def image_member_list(ctx, image_ref):
    data = _api("get", f"{DEFAULT_URLS['image']}/images/{image_ref}/members", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["project_id", "status"])

@image_member.command("remove")
@click.argument("image_ref")
@click.argument("project_id")
@click.pass_context
def image_member_remove(ctx, image_ref, project_id):
    _api("delete", f"{DEFAULT_URLS['image']}/images/{image_ref}/members/{project_id}",
         ctx.obj["profile"])
    click.echo(f"✓ Sharing removed for project {project_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# VOLUME
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def volume(ctx):
    """Manage block storage volumes"""

@volume.command("create")
@click.argument("name")
@click.option("--size", type=int, required=True, help="Size in GB")
@click.option("--type", "volume_type", default=None, help="Volume type name (e.g. gp2, gp3, io2)")
@click.option("--image", help="Create from image (name or ID)")
@click.option("--snapshot", help="Create from snapshot (name or ID)")
@click.option("--source", "source_volume", help="Clone from existing volume")
@click.pass_context
def volume_create(ctx, name, size, volume_type, image, snapshot, source_volume):
    payload: dict = {"name": name, "size": size}
    if volume_type:   payload["volume_type"] = volume_type
    if image:         payload["image_id"] = image
    if snapshot:      payload["snapshot"] = snapshot
    if source_volume: payload["source_volume"] = source_volume
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@volume.command("list")
@click.pass_context
def volume_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['volume']}/volumes", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"],
            ["id", "name", "size", "status", "type", "bootable", "availability_zone"])

@volume.command("show")
@click.argument("ref")
@click.pass_context
def volume_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['volume']}/volumes/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@volume.command("delete")
@click.argument("ref")
@click.pass_context
def volume_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['volume']}/volumes/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Volume {ref} deleted")

@volume.command("extend")
@click.argument("ref")
@click.option("--size", type=int, required=True, help="New size in GB")
@click.pass_context
def volume_extend(ctx, ref, size):
    _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/extend",
         ctx.obj["profile"], json={"new_size": size})
    click.echo(f"✓ Volume {ref} extended to {size} GB")

@volume.command("attach")
@click.argument("ref")
@click.option("--server", required=True)
@click.pass_context
def volume_attach(ctx, ref, server):
    _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/attach",
         ctx.obj["profile"], json={"server_id": server})
    click.echo(f"✓ Volume {ref} attached to {server}")

@volume.command("detach")
@click.argument("ref")
@click.pass_context
def volume_detach(ctx, ref):
    _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/detach", ctx.obj["profile"])
    click.echo(f"✓ Volume {ref} detached")

@volume.command("snapshot")
@click.argument("ref")
@click.option("--name", required=True)
@click.pass_context
def volume_snapshot(ctx, ref, name):
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/snapshots",
                ctx.obj["profile"], json={"name": name})
    _output(data, ctx.obj["fmt"])

@volume.command("backup")
@click.argument("ref")
@click.option("--name", required=True)
@click.pass_context
def volume_backup(ctx, ref, name):
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/backup",
                ctx.obj["profile"], json={"name": name})
    _output(data, ctx.obj["fmt"])

@volume.command("transfer-create")
@click.argument("ref")
@click.option("--name", required=True)
@click.pass_context
def volume_transfer_create(ctx, ref, name):
    """Create a transfer token to move volume ownership"""
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/transfer",
                ctx.obj["profile"], json={"name": name})
    click.echo(f"✓ Transfer created. Share this auth_key: {data.get('auth_key', '?')}")
    _output(data, ctx.obj["fmt"])

@volume.command("transfer-accept")
@click.argument("transfer_id")
@click.option("--auth-key", required=True)
@click.pass_context
def volume_transfer_accept(ctx, transfer_id, auth_key):
    data = _api("post", f"{DEFAULT_URLS['volume']}/volume-transfers/{transfer_id}/accept",
                ctx.obj["profile"], json={"auth_key": auth_key})
    click.echo(f"✓ Volume transfer accepted")
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# VOLUME TYPE
# Volume types are AWS EBS-style performance profiles (gp2, gp3, io1, io2, sc1, st1, etc.)
# Admin must create them before any volumes can be created.
# Backend (lvm/ceph) is the storage infrastructure — separate from the type name.
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("volume-type")
@click.pass_context
def volume_type(ctx):
    """Manage volume types (AWS EBS-style performance profiles)"""

@volume_type.command("create")
@click.argument("name", metavar="NAME")
@click.option("--backend", type=click.Choice(["lvm", "ceph"]), required=True,
              help="Storage backend: lvm (block device) or ceph (RBD network disk)")
@click.option("--disk-type", "disk_type",
              type=click.Choice(["sata_ssd", "sas_ssd", "nvme_ssd", "sata_hdd", "sas_hdd"]),
              default="sata_ssd", show_default=True,
              help="Hardware tier hint (informational)")
@click.option("--max-iops", "max_iops", type=int, default=None,
              help="Max IOPS (informational, not enforced). E.g. 3000")
@click.option("--max-throughput", "max_throughput", type=int, default=None,
              help="Max throughput in MB/s (informational). E.g. 125")
@click.option("--description", "-d", default=None, help="Human-readable description")
@click.option("--private", is_flag=True, default=False, help="Make type private (admin-only)")
@click.pass_context
def vt_create(ctx, name, backend, disk_type, max_iops, max_throughput, description, private):
    """Create a new volume type.

    \b
    Examples (AWS EBS equivalents):
      cloudx volume-type create gp2  --backend lvm  --disk-type sata_ssd --max-iops 3000  --max-throughput 125
      cloudx volume-type create gp3  --backend lvm  --disk-type sas_ssd  --max-iops 16000 --max-throughput 1000
      cloudx volume-type create io1  --backend lvm  --disk-type nvme_ssd --max-iops 64000 --max-throughput 4000
      cloudx volume-type create io2  --backend ceph --disk-type nvme_ssd --max-iops 64000 --max-throughput 4000
      cloudx volume-type create sc1  --backend lvm  --disk-type sata_hdd --max-iops 250   --max-throughput 250
      cloudx volume-type create st1  --backend lvm  --disk-type sas_hdd  --max-iops 500   --max-throughput 500
    """
    payload = {
        "name":           name,
        "backend":        backend,
        "disk_type":      disk_type,
        "max_iops":       max_iops,
        "max_throughput": max_throughput,
        "description":    description,
        "is_public":      not private,
    }
    data = _api("post", f"{DEFAULT_URLS['volume']}/volume-types", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@volume_type.command("list")
@click.pass_context
def vt_list(ctx):
    """List all volume types."""
    data = _api("get", f"{DEFAULT_URLS['volume']}/volume-types", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"],
            ["name", "disk_type", "backend", "max_iops", "max_throughput", "description", "is_public"])

@volume_type.command("show")
@click.argument("ref")
@click.pass_context
def vt_show(ctx, ref):
    """Show details of a volume type."""
    data = _api("get", f"{DEFAULT_URLS['volume']}/volume-types/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@volume_type.command("set")
@click.argument("ref")
@click.option("--description", "-d", default=None)
@click.option("--max-iops", "max_iops", type=int, default=None)
@click.option("--max-throughput", "max_throughput", type=int, default=None)
@click.option("--public/--private", default=None)
@click.pass_context
def vt_set(ctx, ref, description, max_iops, max_throughput, public):
    """Update mutable fields of a volume type (admin only)."""
    payload: dict = {}
    if description is not None:    payload["description"] = description
    if max_iops is not None:       payload["max_iops"] = max_iops
    if max_throughput is not None: payload["max_throughput"] = max_throughput
    if public is not None:         payload["is_public"] = public
    if not payload:
        click.echo("Nothing to update. Use --description, --max-iops, --max-throughput, or --public/--private.")
        return
    data = _api("patch", f"{DEFAULT_URLS['volume']}/volume-types/{ref}", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@volume_type.command("delete")
@click.argument("ref")
@click.pass_context
def vt_delete(ctx, ref):
    """Delete a volume type (admin only). Fails if any volumes use this type."""
    _api("delete", f"{DEFAULT_URLS['volume']}/volume-types/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Volume type {ref} deleted")



# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT (volume)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def snapshot(ctx):
    """Manage volume snapshots"""

@snapshot.command("list")
@click.pass_context
def snapshot_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['volume']}/snapshots", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "volume_id", "size", "status"])

@snapshot.command("show")
@click.argument("ref")
@click.pass_context
def snapshot_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['volume']}/snapshots/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@snapshot.command("delete")
@click.argument("ref")
@click.pass_context
def snapshot_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['volume']}/snapshots/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Snapshot {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# VOLUME BACKUP (Cinder backup to object storage)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("backup")
@click.pass_context
def volume_backup(ctx):
    """Manage full volume backups to object storage"""

@volume_backup.command("list")
@click.pass_context
def volume_backup_list(ctx):
    """List all volume backups"""
    data = _api("get", f"{DEFAULT_URLS['volume']}/backups", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "volume_id", "size_gb", "status"])

@volume_backup.command("show")
@click.argument("ref")
@click.pass_context
def volume_backup_show(ctx, ref):
    """Show details of a volume backup"""
    data = _api("get", f"{DEFAULT_URLS['volume']}/backups/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@volume_backup.command("create")
@click.argument("volume_ref")
@click.option("--name", default=None, help="Backup name")
@click.option("--description", default=None, help="Backup description")
@click.pass_context
def volume_backup_create(ctx, volume_ref, name, description):
    """Create a backup of a volume to object storage"""
    payload = {}
    if name: payload["name"] = name
    if description: payload["description"] = description
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes/{volume_ref}/backup", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@volume_backup.command("restore")
@click.argument("backup_id")
@click.option("--name", default=None, help="Name for the restored volume")
@click.pass_context
def volume_backup_restore(ctx, backup_id, name):
    """Restore a backup into a new volume"""
    payload = {}
    if name: payload["name"] = name
    data = _api("post", f"{DEFAULT_URLS['volume']}/backups/{backup_id}/restore", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@volume_backup.command("delete")
@click.argument("backup_id")
@click.pass_context
def volume_backup_delete(ctx, backup_id):
    """Delete a volume backup"""
    _api("delete", f"{DEFAULT_URLS['volume']}/backups/{backup_id}", ctx.obj["profile"])
    click.echo(f"✓ Backup {backup_id} deletion requested")


# ═══════════════════════════════════════════════════════════════════════════════
# HYPERVISOR (Compute nodes & physical host status)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("hypervisor")
@click.pass_context
def hypervisor(ctx):
    """Manage and inspect hypervisors and compute nodes"""

@hypervisor.command("list")
@click.pass_context
def hypervisor_list(ctx):
    """List all compute hypervisor nodes"""
    data = _api("get", f"{DEFAULT_URLS['placement']}/compute_nodes", ctx.obj["profile"])
    nodes = data.get("compute_nodes", [])
    _output(nodes, ctx.obj["fmt"], ["host", "name", "status", "running_vms", "cpu_percent_used", "memory_mb_used", "memory_mb_total"])

@hypervisor.command("show")
@click.argument("host")
@click.pass_context
def hypervisor_show(ctx, host):
    """Show details of a specific hypervisor compute node"""
    data = _api("get", f"{DEFAULT_URLS['placement']}/compute_nodes", ctx.obj["profile"])
    nodes = data.get("compute_nodes", [])
    target = next((n for n in nodes if n.get("host") == host or n.get("name") == host or n.get("resource_provider_uuid") == host), None)
    if not target:
        click.echo(f"❌ Hypervisor node '{host}' not found", err=True)
        ctx.exit(1)
    _output(target, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# PLACEMENT
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def placement(ctx):
    """View resource placement and usage"""

@placement.command("usage")
@click.pass_context
def placement_usage(ctx):
    data = _api("get", f"{DEFAULT_URLS['placement']}/usages", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@placement.command("provider-list")
@click.pass_context
def provider_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['placement']}/resource_providers", ctx.obj["profile"])
    _output(data.get("resource_providers", []), ctx.obj["fmt"],
            ["uuid", "name", "generation"])

@placement.command("provider-show")
@click.argument("uuid")
@click.pass_context
def provider_show(ctx, uuid):
    data = _api("get", f"{DEFAULT_URLS['placement']}/resource_providers/{uuid}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# HOST
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def host(ctx):
    """Host / hypervisor management"""

@host.command("check")
@click.pass_context
def host_check(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/host/check", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@host.command("show")
@click.pass_context
def host_show(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/host", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# LOAD BALANCER
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("load-balancer")
@click.pass_context
def load_balancer(ctx):
    """Manage load balancers (NLB L4 & ALB L7)"""

@load_balancer.command("list")
@click.pass_context
def lb_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/load-balancers", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"],
            ["id", "name", "lb_type", "vip_address", "provisioning_status", "operating_status"])

@load_balancer.command("show")
@click.argument("ref")
@click.pass_context
def lb_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@load_balancer.command("create")
@click.argument("name")
@click.option("--network", required=True, help="Network name or ID")
@click.option("--subnet", default=None, help="Subnet name or ID")
@click.option("--type", "lb_type", type=click.Choice(["nlb", "alb"]), default="nlb")
@click.pass_context
def lb_create(ctx, name, network, subnet, lb_type):
    payload = {"name": name, "vip_network_id": network, "lb_type": lb_type}
    if subnet: payload["vip_subnet_id"] = subnet
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/load-balancers", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@load_balancer.command("delete")
@click.argument("ref")
@click.pass_context
def lb_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Load balancer {ref} deleted")


# ── Load Balancer Listeners ───────────────────────────────────────────────────
@load_balancer.group("listener")
@click.pass_context
def lb_listener(ctx):
    """Manage load balancer listeners"""

@lb_listener.command("list")
@click.argument("lb_ref")
@click.pass_context
def listener_list(ctx, lb_ref):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{lb_ref}/listeners", ctx.obj["profile"])
    _output(data.get("listeners", data.get("items", data)), ctx.obj["fmt"], ["id", "name", "protocol", "protocol_port", "default_pool_id"])

@lb_listener.command("create")
@click.argument("lb_ref")
@click.option("--name", required=True, help="Listener name")
@click.option("--port", required=True, type=int, help="Protocol port (e.g. 80, 443)")
@click.option("--protocol", type=click.Choice(["HTTP", "TCP", "UDP"]), default="HTTP", help="Protocol")
@click.pass_context
def listener_create(ctx, lb_ref, name, port, protocol):
    payload = {"name": name, "protocol": protocol, "protocol_port": port}
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{lb_ref}/listeners", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


# ── Load Balancer Pools ───────────────────────────────────────────────────────
@load_balancer.group("pool")
@click.pass_context
def lb_pool(ctx):
    """Manage load balancer pools and members"""

@lb_pool.command("create")
@click.argument("listener_ref")
@click.option("--name", required=True, help="Pool name")
@click.option("--algorithm", type=click.Choice(["ROUND_ROBIN", "LEAST_CONNECTIONS", "SOURCE_IP"]), default="ROUND_ROBIN")
@click.option("--server-group", default=None, help="Server Group ID to auto-sync members")
@click.pass_context
def pool_create(ctx, listener_ref, name, algorithm, server_group):
    payload = {"name": name, "lb_algorithm": algorithm}
    if server_group: payload["server_group_id"] = server_group
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/listeners/{listener_ref}/pools", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@lb_pool.command("show")
@click.argument("pool_ref")
@click.pass_context
def pool_show(ctx, pool_ref):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/pools/{pool_ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@lb_pool.command("sync")
@click.argument("pool_ref")
@click.pass_context
def pool_sync(ctx, pool_ref):
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/pools/{pool_ref}/sync", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ── Load Balancer Health Monitors ──────────────────────────────────────────────
@load_balancer.group("monitor")
@click.pass_context
def lb_monitor(ctx):
    """Manage health monitors for pools"""

@lb_monitor.command("create")
@click.argument("pool_ref")
@click.option("--type", "type_", type=click.Choice(["HTTP", "TCP", "PING"]), default="HTTP", help="Monitor type")
@click.option("--delay", default=5, type=int, help="Check interval in seconds")
@click.option("--timeout", default=5, type=int, help="Timeout in seconds")
@click.option("--max-retries", default=3, type=int, help="Max retries before offline")
@click.option("--url-path", default="/", help="URL path for HTTP monitor")
@click.pass_context
def monitor_create(ctx, pool_ref, type_, delay, timeout, max_retries, url_path):
    payload = {
        "type": type_,
        "delay": delay,
        "timeout": timeout,
        "max_retries": max_retries,
        "url_path": url_path,
    }
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/pools/{pool_ref}/health-monitors", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# OBJECT STORE
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("object-store")
@click.pass_context
def object_store(ctx):
    """Manage object storage buckets and objects"""

@object_store.command("list")
@click.pass_context
def bucket_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['object-store']}/buckets", ctx.obj["profile"])
    _output(data.get("buckets", data), ctx.obj["fmt"],
            ["id", "name", "visibility", "endpoint_url", "object_count", "size_bytes", "created_at"])

@object_store.command("show")
@click.argument("name")
@click.pass_context
def bucket_show(ctx, name):
    data = _api("get", f"{DEFAULT_URLS['object-store']}/buckets/{name}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@object_store.command("create")
@click.argument("name")
@click.option("--visibility", type=click.Choice(["private", "public-read"]), default="private", help="Bucket visibility")
@click.option("--type-id", help="Bucket type ID (optional)")
@click.pass_context
def bucket_create(ctx, name, visibility, type_id):
    payload = {"name": name, "visibility": visibility}
    if type_id: payload["bucket_type_id"] = type_id
    data = _api("post", f"{DEFAULT_URLS['object-store']}/buckets", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@object_store.command("set-visibility")
@click.argument("name")
@click.argument("visibility", type=click.Choice(["private", "public-read"]))
@click.pass_context
def bucket_set_visibility(ctx, name, visibility):
    data = _api("patch", f"{DEFAULT_URLS['object-store']}/buckets/{name}", ctx.obj["profile"], json={"visibility": visibility})
    _output(data, ctx.obj["fmt"])

@object_store.command("delete")
@click.argument("name")
@click.pass_context
def bucket_delete(ctx, name):
    _api("delete", f"{DEFAULT_URLS['object-store']}/buckets/{name}", ctx.obj["profile"])
    click.echo(f"✓ Bucket {name} deleted")


# ── Object Store Objects ────────────────═══════════════════════════════════════
@object_store.group("object")
@click.pass_context
def object_cmd(ctx):
    """Manage objects inside buckets"""

@object_cmd.command("list")
@click.argument("bucket")
@click.option("--prefix", default="", help="Prefix filter")
@click.pass_context
def object_list(ctx, bucket, prefix):
    url = f"{DEFAULT_URLS['object-store']}/buckets/{bucket}/objects"
    if prefix: url += f"?prefix={prefix}"
    data = _api("get", url, ctx.obj["profile"])
    _output(data.get("objects", data), ctx.obj["fmt"], ["id", "key", "size", "content_type", "created_at"])

@object_cmd.command("put")
@click.argument("bucket")
@click.argument("local_file", type=click.Path(exists=True))
@click.option("--key", default=None, help="Object key in bucket (defaults to local file name)")
@click.pass_context
def object_put(ctx, bucket, local_file, key):
    key = key or os.path.basename(local_file)
    headers = _headers(ctx.obj["profile"])
    headers.pop("Content-Type", None)  # let requests set multipart boundary
    with open(local_file, "rb") as f:
        files = {"file": (key, f)}
        r = requests.put(f"{DEFAULT_URLS['object-store']}/buckets/{bucket}/objects/{key}", headers=headers, files=files, timeout=60)
        if not r.ok:
            click.echo(f"❌ Upload failed (HTTP {r.status_code}): {r.text}", err=True)
            sys.exit(1)
        _output(r.json(), ctx.obj["fmt"])

@object_cmd.command("get")
@click.argument("bucket")
@click.argument("key")
@click.option("--out", default=None, help="Output file path (defaults to printing to stdout or saving by key)")
@click.pass_context
def object_get(ctx, bucket, key, out):
    headers = _headers(ctx.obj["profile"])
    r = requests.get(f"{DEFAULT_URLS['object-store']}/buckets/{bucket}/objects/{key}", headers=headers, timeout=60)
    if not r.ok:
        click.echo(f"❌ Download failed (HTTP {r.status_code}): {r.text}", err=True)
        sys.exit(1)
    if out:
        with open(out, "wb") as f:
            f.write(r.content)
        click.echo(f"✓ Object {key} saved to {out}")
    else:
        sys.stdout.buffer.write(r.content)

@object_cmd.command("delete")
@click.argument("bucket")
@click.argument("key")
@click.pass_context
def object_delete(ctx, bucket, key):
    _api("delete", f"{DEFAULT_URLS['object-store']}/buckets/{bucket}/objects/{key}", ctx.obj["profile"])
    click.echo(f"✓ Object {key} deleted from bucket {bucket}")


# ── Object Store Bucket Types (Admin) ═════════════════════════════════════════
@object_store.group("type")
@click.pass_context
def bucket_type_cmd(ctx):
    """Manage bucket types (admin)"""

@bucket_type_cmd.command("list")
@click.pass_context
def type_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['object-store']}/admin/bucket-types", ctx.obj["profile"])
    _output(data.get("bucket_types", data), ctx.obj["fmt"], ["id", "name", "driver", "description"])

@bucket_type_cmd.command("create")
@click.argument("name")
@click.option("--driver", type=click.Choice(["filesystem", "s3"]), default="filesystem", help="Storage driver")
@click.option("--config", default="{}", help="Config JSON string")
@click.option("--description", default=None, help="Description")
@click.pass_context
def type_create(ctx, name, driver, config, description):
    try:
        cfg_dict = json.loads(config)
    except Exception as exc:
        click.echo(f"❌ Invalid JSON config: {exc}", err=True)
        sys.exit(1)
    payload = {"name": name, "driver": driver, "config": cfg_dict, "description": description}
    data = _api("post", f"{DEFAULT_URLS['object-store']}/admin/bucket-types", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@bucket_type_cmd.command("delete")
@click.argument("ref")
@click.pass_context
def type_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['object-store']}/admin/bucket-types/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Bucket type {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# TELEMETRY
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("telemetry")
@click.pass_context
def telemetry(ctx):
    """Observability metrics & Prometheus export"""

@telemetry.command("metric-type-list")
@click.pass_context
def telemetry_metric_type_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/metric-types", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["name", "unit", "description", "default_enabled"])

@telemetry.command("metric-list")
@click.option("--type", "metric_type")
@click.option("--instance", "instance_id")
@click.option("--limit", type=int, default=50)
@click.pass_context
def telemetry_metric_list(ctx, metric_type, instance_id, limit):
    params = f"?limit={limit}"
    if metric_type:  params += f"&metric_type={metric_type}"
    if instance_id:  params += f"&instance_id={instance_id}"
    data = _api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/metrics{params}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "instance_id", "metric_type", "value", "unit", "timestamp"])

@telemetry.command("metric-create")
@click.option("--instance", "instance_id", required=True)
@click.option("--type", "metric_type", required=True)
@click.option("--value", type=float, required=True)
@click.option("--unit", default=None)
@click.option("--host", default=None)
@click.pass_context
def telemetry_metric_create(ctx, instance_id, metric_type, value, unit, host):
    payload = {"instance_id": instance_id, "metric_type": metric_type, "value": value}
    if unit: payload["unit"] = unit
    if host: payload["host"] = host
    data = _api("post", f"{DEFAULT_URLS['telemetry']}/telemetry/metrics", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@telemetry.command("stats")
@click.pass_context
def telemetry_stats(ctx):
    data = _api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/stats", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@telemetry.command("server-stats")
@click.argument("instance_id")
@click.pass_context
def telemetry_server_stats(ctx, instance_id):
    data = _api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/instances/{instance_id}/stats", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@telemetry.command("prometheus")
@click.pass_context
def telemetry_prometheus(ctx):
    """Dump Prometheus exposition format to stdout"""
    token = _get_token(ctx.obj["profile"])
    headers = {"X-Auth-Token": token} if token else {}
    r = requests.get(f"{DEFAULT_URLS['telemetry']}/metrics", headers=headers, timeout=30)
    if not r.ok:
        click.echo(f"❌ Prometheus export failed (HTTP {r.status_code}): {r.text}", err=True)
        sys.exit(1)
    click.echo(r.text)


# ═══════════════════════════════════════════════════════════════════════════════
# BILLING
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("billing")
@click.pass_context
def billing(ctx):
    """Usage rating, invoices, and PDF generation"""

@billing.command("rating-sheet-list")
@click.pass_context
def billing_rating_sheet_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['billing']}/billing/rating-sheets", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "resource_type", "unit", "rate_per_unit", "currency", "is_active"])

@billing.command("rating-sheet-create")
@click.option("--resource-type", required=True)
@click.option("--unit", default="hour")
@click.option("--rate-per-unit", type=float, required=True)
@click.option("--currency", default="IDR")
@click.option("--description", default=None)
@click.pass_context
def billing_rating_sheet_create(ctx, resource_type, unit, rate_per_unit, currency, description):
    payload = {"resource_type": resource_type, "unit": unit, "rate_per_unit": rate_per_unit,
               "currency": currency}
    if description: payload["description"] = description
    data = _api("post", f"{DEFAULT_URLS['billing']}/billing/rating-sheets", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@billing.command("usage-list")
@click.option("--project")
@click.option("--limit", type=int, default=50)
@click.pass_context
def billing_usage_list(ctx, project, limit):
    params = f"?limit={limit}"
    if project: params += f"&project_id={project}"
    data = _api("get", f"{DEFAULT_URLS['billing']}/billing/usage-records{params}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "project_id", "resource_type", "resource_id", "quantity", "unit", "total_cost"])

@billing.command("usage-create")
@click.option("--project", "project_id", required=True)
@click.option("--resource-type", required=True)
@click.option("--resource-id", default=None)
@click.option("--quantity", type=float, required=True)
@click.option("--unit", default="hour")
@click.option("--rate-per-unit", type=float, required=True)
@click.option("--period-start", required=True)
@click.option("--period-end", required=True)
@click.pass_context
def billing_usage_create(ctx, project_id, resource_type, resource_id, quantity, unit, rate_per_unit, period_start, period_end):
    payload = {
        "project_id": project_id,
        "resource_type": resource_type,
        "quantity": quantity,
        "unit": unit,
        "rate_per_unit": rate_per_unit,
        "period_start": period_start,
        "period_end": period_end,
    }
    if resource_id: payload["resource_id"] = resource_id
    data = _api("post", f"{DEFAULT_URLS['billing']}/billing/usage-records", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@billing.command("invoice-list")
@click.option("--project")
@click.option("--status")
@click.pass_context
def billing_invoice_list(ctx, project, status):
    params = "?"
    if project: params += f"project_id={project}&"
    if status:  params += f"status={status}&"
    data = _api("get", f"{DEFAULT_URLS['billing']}/billing/invoices{params}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "invoice_number", "project_id", "status", "subtotal", "tax_amount", "total", "created_at"])

@billing.command("invoice-create")
@click.option("--project", "project_id")
@click.option("--period-start")
@click.option("--period-end")
@click.option("--notes")
@click.pass_context
def billing_invoice_create(ctx, project_id, period_start, period_end, notes):
    params = "?"
    if project_id:   params += f"project_id={project_id}&"
    if period_start: params += f"period_start={period_start}&"
    if period_end:   params += f"period_end={period_end}&"
    if notes:        params += f"notes={notes}&"
    data = _api("post", f"{DEFAULT_URLS['billing']}/billing/invoices{params}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@billing.command("invoice-pdf")
@click.argument("invoice_id")
@click.option("--out", default=None, help="Output file path (.pdf or .html)")
@click.pass_context
def billing_invoice_pdf(ctx, invoice_id, out):
    data = _api("post", f"{DEFAULT_URLS['billing']}/billing/invoices/{invoice_id}/pdf", ctx.obj["profile"])
    if "pdf_base64" in data:
        import base64
        out_file = out or f"invoice-{invoice_id}.pdf"
        with open(out_file, "wb") as f:
            f.write(base64.b64decode(data["pdf_base64"]))
        click.echo(f"✓ Invoice PDF saved to {out_file}")
    elif "html" in data:
        out_file = out or f"invoice-{invoice_id}.html"
        with open(out_file, "w") as f:
            f.write(data["html"])
        click.echo(f"✓ Invoice HTML saved to {out_file} (WeasyPrint not installed on server)")
    else:
        _output(data, ctx.obj["fmt"])

@billing.command("summary")
@click.argument("project_id", required=False)
@click.pass_context
def billing_summary(ctx, project_id):
    url = f"{DEFAULT_URLS['billing']}/billing/summary"
    if project_id:
        url += f"/{project_id}"
    data = _api("get", url, ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# EVENT
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("event")
@click.pass_context
def event(ctx):
    """Event log and webhook subscription management"""

@event.command("emit")
@click.option("--type", "event_type", required=True)
@click.option("--resource-type", default="instance")
@click.option("--resource-id")
@click.option("--payload", default="{}")
@click.pass_context
def event_emit(ctx, event_type, resource_type, resource_id, payload):
    try:
        body = json.loads(payload)
    except Exception as exc:
        click.echo(f"❌ Invalid JSON payload: {exc}", err=True)
        sys.exit(1)
    data = {"event_type": event_type, "resource_type": resource_type, "payload": body}
    if resource_id: data["resource_id"] = resource_id
    out = _api("post", f"{DEFAULT_URLS['event']}/events/emit", ctx.obj["profile"], json=data)
    _output(out, ctx.obj["fmt"])

@event.command("type-list")
@click.pass_context
def event_type_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['event']}/events/types", ctx.obj["profile"])
    items = data.get("event_types", data) if isinstance(data, dict) else data
    _output(items, ctx.obj["fmt"], ["event_type", "type", "description"])

@event.command("log-list")
@click.option("--type")
@click.option("--limit", type=int, default=50)
@click.pass_context
def event_log_list(ctx, type, limit):
    params = f"?limit={limit}"
    if type: params += f"&event_type={type}"
    data = _api("get", f"{DEFAULT_URLS['event']}/events/logs{params}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "event_type", "resource_type", "resource_id", "timestamp"])

@event.group("subscription")
@click.pass_context
def event_subscription(ctx):
    """Manage webhook subscriptions"""

@event_subscription.command("create")
@click.argument("name")
@click.option("--endpoint-url", required=True)
@click.option("--event-type", "event_types", multiple=True, required=True)
@click.option("--secret")
@click.pass_context
def event_subscription_create(ctx, name, endpoint_url, event_types, secret):
    payload = {"name": name, "endpoint_url": endpoint_url, "event_types": list(event_types)}
    if secret: payload["secret"] = secret
    data = _api("post", f"{DEFAULT_URLS['event']}/events/subscriptions", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@event_subscription.command("list")
@click.pass_context
def event_subscription_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['event']}/events/subscriptions", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "endpoint_url", "event_types", "is_active"])

@event_subscription.command("show")
@click.argument("sub_id")
@click.pass_context
def event_subscription_show(ctx, sub_id):
    data = _api("get", f"{DEFAULT_URLS['event']}/events/subscriptions/{sub_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@event_subscription.command("delete")
@click.argument("sub_id")
@click.pass_context
def event_subscription_delete(ctx, sub_id):
    _api("delete", f"{DEFAULT_URLS['event']}/events/subscriptions/{sub_id}", ctx.obj["profile"])
    click.echo(f"✓ Subscription {sub_id} deleted")

@event.command("delivery-list")
@click.pass_context
def event_delivery_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['event']}/events/deliveries", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "subscription_id", "event_id", "status", "response_code", "attempts", "created_at"])

@event.command("delivery-retry")
@click.argument("delivery_id")
@click.pass_context
def event_delivery_retry(ctx, delivery_id):
    data = _api("post", f"{DEFAULT_URLS['event']}/events/deliveries/{delivery_id}/retry", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION / AUTO-SCALING
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("scaling-group")
@click.pass_context
def scaling_group(ctx):
    """Auto-scaling group orchestration"""

@scaling_group.command("list")
@click.pass_context
def scaling_group_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "min_instances", "max_instances", "target_cpu_percent", "status"])

@scaling_group.command("show")
@click.argument("group_id")
@click.pass_context
def scaling_group_show(ctx, group_id):
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@scaling_group.command("create")
@click.argument("name")
@click.option("--image", "image_id", required=True)
@click.option("--flavor", "flavor_id", required=True)
@click.option("--network", "network_id", required=True)
@click.option("--subnet", "subnet_id")
@click.option("--min-instances", type=int, default=1)
@click.option("--max-instances", type=int, default=5)
@click.option("--target-cpu", "target_cpu_percent", type=float, default=70.0)
@click.option("--cooldown", "cooldown_seconds", type=int, default=300)
@click.option("--scale-in-cooldown", "scale_in_cooldown_seconds", type=int, default=600)
@click.option("--description", default=None)
@click.pass_context
def scaling_group_create(ctx, name, image_id, flavor_id, network_id, subnet_id, min_instances,
                         max_instances, target_cpu_percent, cooldown_seconds, scale_in_cooldown_seconds, description):
    payload = {
        "name": name,
        "image_id": image_id,
        "flavor_id": flavor_id,
        "network_id": network_id,
        "min_instances": min_instances,
        "max_instances": max_instances,
        "target_cpu_percent": target_cpu_percent,
        "cooldown_seconds": cooldown_seconds,
        "scale_in_cooldown_seconds": scale_in_cooldown_seconds,
    }
    if subnet_id:   payload["subnet_id"] = subnet_id
    if description: payload["description"] = description
    data = _api("post", f"{DEFAULT_URLS['orchestration']}/scaling-groups", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@scaling_group.command("update")
@click.argument("group_id")
@click.option("--min-instances", type=int, default=None)
@click.option("--max-instances", type=int, default=None)
@click.option("--target-cpu", "target_cpu_percent", type=float, default=None)
@click.option("--cooldown", "cooldown_seconds", type=int, default=None)
@click.pass_context
def scaling_group_update(ctx, group_id, min_instances, max_instances, target_cpu_percent, cooldown_seconds):
    payload = {}
    if min_instances is not None:      payload["min_instances"] = min_instances
    if max_instances is not None:      payload["max_instances"] = max_instances
    if target_cpu_percent is not None: payload["target_cpu_percent"] = target_cpu_percent
    if cooldown_seconds is not None:   payload["cooldown_seconds"] = cooldown_seconds
    if not payload:
        click.echo("Nothing to update.")
        return
    data = _api("patch", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@scaling_group.command("delete")
@click.argument("group_id")
@click.pass_context
def scaling_group_delete(ctx, group_id):
    _api("delete", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}", ctx.obj["profile"])
    click.echo(f"✓ Scaling group {group_id} deleted")

@scaling_group.command("scale-out")
@click.argument("group_id")
@click.pass_context
def scaling_group_scale_out(ctx, group_id):
    data = _api("post", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}/scale-out", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@scaling_group.command("scale-in")
@click.argument("group_id")
@click.pass_context
def scaling_group_scale_in(ctx, group_id):
    data = _api("post", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}/scale-in", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@scaling_group.command("instance-list")
@click.argument("group_id")
@click.pass_context
def scaling_group_instance_list(ctx, group_id):
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}/instances", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "server_id", "server_name", "status", "created_at"])

@scaling_group.command("history")
@click.argument("group_id")
@click.pass_context
def scaling_group_history(ctx, group_id):
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}/history", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "action", "reason", "server_id", "avg_cpu_before", "instance_count_after", "created_at"])


@cli.group("stack")
@click.pass_context
def stack_group(ctx):
    """Orchestration stack and resource management"""

@stack_group.command("list")
@click.pass_context
def stack_list(ctx):
    """List orchestration stacks / auto-scaling groups"""
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "min_instances", "max_instances", "status"])

@stack_group.command("show")
@click.argument("ref")
@click.pass_context
def stack_show(ctx, ref):
    """Show details of an orchestration stack"""
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@stack_group.command("delete")
@click.argument("ref")
@click.pass_context
def stack_delete(ctx, ref):
    """Delete an orchestration stack"""
    _api("delete", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Orchestration stack {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# VPC PEERING & BGP (Networking extensions)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("vpc-peering")
@click.pass_context
def vpc_peering(ctx):
    """VPC peering between routers"""

@vpc_peering.command("list")
@click.pass_context
def vpc_peering_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpc-peerings", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "requester_router_name", "accepter_router_name", "status"])

@vpc_peering.command("create")
@click.argument("name")
@click.option("--requester", required=True)
@click.option("--accepter", required=True)
@click.pass_context
def vpc_peering_create(ctx, name, requester, accepter):
    payload = {"name": name, "accepter_router_id": accepter}
    data = _api("post", f"{DEFAULT_URLS['network']}/routers/{requester}/peering", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@vpc_peering.command("show")
@click.argument("peering_id")
@click.pass_context
def vpc_peering_show(ctx, peering_id):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpc-peerings/{peering_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@vpc_peering.command("delete")
@click.argument("peering_id")
@click.pass_context
def vpc_peering_delete(ctx, peering_id):
    _api("delete", f"{DEFAULT_URLS['network']}/vpc-peerings/{peering_id}", ctx.obj["profile"])
    click.echo(f"✓ VPC peering {peering_id} deleted")


@cli.group("bgp-peer")
@click.pass_context
def bgp_peer(ctx):
    """BGP peer management for VPC routing"""

@bgp_peer.command("list")
@click.pass_context
def bgp_peer_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/bgp-peers", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "router_id", "peer_ip", "local_as", "remote_as", "status"])

@bgp_peer.command("create")
@click.argument("name")
@click.option("--router", "router_id", required=True)
@click.option("--peer-ip", required=True)
@click.option("--local-as", type=int, required=True)
@click.option("--remote-as", type=int, required=True)
@click.option("--auth-password")
@click.option("--advertised-prefix", "advertised_prefixes", multiple=True)
@click.pass_context
def bgp_peer_create(ctx, name, router_id, peer_ip, local_as, remote_as, auth_password, advertised_prefixes):
    payload = {"name": name, "router_id": router_id, "peer_ip": peer_ip, "local_as": local_as, "remote_as": remote_as}
    if auth_password: payload["auth_password"] = auth_password
    if advertised_prefixes: payload["advertised_prefixes"] = list(advertised_prefixes)
    data = _api("post", f"{DEFAULT_URLS['network']}/bgp-peers", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@bgp_peer.command("show")
@click.argument("peer_id")
@click.pass_context
def bgp_peer_show(ctx, peer_id):
    data = _api("get", f"{DEFAULT_URLS['network']}/bgp-peers/{peer_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@bgp_peer.command("delete")
@click.argument("peer_id")
@click.pass_context
def bgp_peer_delete(ctx, peer_id):
    _api("delete", f"{DEFAULT_URLS['network']}/bgp-peers/{peer_id}", ctx.obj["profile"])
    click.echo(f"✓ BGP peer {peer_id} deleted")

@bgp_peer.command("sync-config")
@click.argument("peer_id")
@click.pass_context
def bgp_peer_sync_config(ctx, peer_id):
    data = _api("post", f"{DEFAULT_URLS['network']}/bgp-peers/{peer_id}/sync-config", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# KEY MANAGER / SECRETS
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("secret")
@click.pass_context
def secret(ctx):
    """Manage encrypted secrets (LUKS keys, certificates, passphrases)"""

@secret.command("list")
@click.pass_context
def secret_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['key-manager']}/secrets", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "secret_type", "algorithm", "bit_length", "project_id"])

@secret.command("create")
@click.argument("name")
@click.option("--payload", required=True, help="Secret payload content / passphrase")
@click.option("--type", "secret_type", default="symmetric", help="symmetric, passphrase, certificate, rsa, opaque")
@click.option("--algorithm", default="aes")
@click.option("--bit-length", type=int, default=256)
@click.pass_context
def secret_create(ctx, name, payload, secret_type, algorithm, bit_length):
    body = {
        "name": name,
        "payload": payload,
        "secret_type": secret_type,
        "algorithm": algorithm,
        "bit_length": bit_length
    }
    data = _api("post", f"{DEFAULT_URLS['key-manager']}/secrets", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])

@secret.command("show")
@click.argument("secret_id")
@click.pass_context
def secret_show(ctx, secret_id):
    data = _api("get", f"{DEFAULT_URLS['key-manager']}/secrets/{secret_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@secret.command("delete")
@click.argument("secret_id")
@click.pass_context
def secret_delete(ctx, secret_id):
    _api("delete", f"{DEFAULT_URLS['key-manager']}/secrets/{secret_id}", ctx.obj["profile"])
    click.echo(f"✓ Secret {secret_id} deleted")


@cli.group("secret-container")
@click.pass_context
def secret_container(ctx):
    """Manage secret containers (bundles of secrets)"""

@secret_container.command("list")
@click.pass_context
def secret_container_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['key-manager']}/containers", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "container_type", "secret_refs", "project_id"])

@secret_container.command("create")
@click.argument("name")
@click.option("--type", "container_type", default="generic")
@click.option("--secret", "secret_refs", multiple=True)
@click.pass_context
def secret_container_create(ctx, name, container_type, secret_refs):
    body = {
        "name": name,
        "container_type": container_type,
        "secret_refs": list(secret_refs)
    }
    data = _api("post", f"{DEFAULT_URLS['key-manager']}/containers", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])

@secret_container.command("show")
@click.argument("container_id")
@click.pass_context
def secret_container_show(ctx, container_id):
    data = _api("get", f"{DEFAULT_URLS['key-manager']}/containers/{container_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@secret_container.command("delete")
@click.argument("container_id")
@click.pass_context
def secret_container_delete(ctx, container_id):
    _api("delete", f"{DEFAULT_URLS['key-manager']}/containers/{container_id}", ctx.obj["profile"])
    click.echo(f"✓ Secret container {container_id} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# VPNAAS (IPsec VPN)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("vpn")
@click.pass_context
def vpn(ctx):
    """IPsec Site-to-Site VPN management"""

@vpn.group("ikepolicy")
@click.pass_context
def vpn_ikepolicy(ctx):
    """IKE Policy management"""

@vpn_ikepolicy.command("list")
@click.pass_context
def vpn_ikepolicy_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpn/ikepolicies", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "auth_algorithm", "encryption_algorithm", "phase1_negotiation_mode", "pfs"])

@vpn_ikepolicy.command("create")
@click.argument("name")
@click.option("--auth-algo", default="sha256")
@click.option("--enc-algo", default="aes-256")
@click.option("--pfs", default="group14")
@click.pass_context
def vpn_ikepolicy_create(ctx, name, auth_algo, enc_algo, pfs):
    body = {"name": name, "auth_algorithm": auth_algo, "encryption_algorithm": enc_algo, "pfs": pfs}
    data = _api("post", f"{DEFAULT_URLS['network']}/vpn/ikepolicies", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])

@vpn_ikepolicy.command("delete")
@click.argument("policy_id")
@click.pass_context
def vpn_ikepolicy_delete(ctx, policy_id):
    _api("delete", f"{DEFAULT_URLS['network']}/vpn/ikepolicies/{policy_id}", ctx.obj["profile"])
    click.echo(f"✓ IKE Policy {policy_id} deleted")


@vpn.group("ipsecpolicy")
@click.pass_context
def vpn_ipsecpolicy(ctx):
    """IPsec Policy management"""

@vpn_ipsecpolicy.command("list")
@click.pass_context
def vpn_ipsecpolicy_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpn/ipsecpolicies", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "auth_algorithm", "encryption_algorithm", "pfs", "transform_protocol"])

@vpn_ipsecpolicy.command("create")
@click.argument("name")
@click.option("--auth-algo", default="sha256")
@click.option("--enc-algo", default="aes-256")
@click.option("--pfs", default="group14")
@click.pass_context
def vpn_ipsecpolicy_create(ctx, name, auth_algo, enc_algo, pfs):
    body = {"name": name, "auth_algorithm": auth_algo, "encryption_algorithm": enc_algo, "pfs": pfs}
    data = _api("post", f"{DEFAULT_URLS['network']}/vpn/ipsecpolicies", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])

@vpn_ipsecpolicy.command("delete")
@click.argument("policy_id")
@click.pass_context
def vpn_ipsecpolicy_delete(ctx, policy_id):
    _api("delete", f"{DEFAULT_URLS['network']}/vpn/ipsecpolicies/{policy_id}", ctx.obj["profile"])
    click.echo(f"✓ IPsec Policy {policy_id} deleted")


@vpn.group("connection")
@click.pass_context
def vpn_connection(ctx):
    """VPN Site Connection management"""

@vpn_connection.command("list")
@click.pass_context
def vpn_connection_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpn/site-connections", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "router_id", "peer_address", "peer_cidrs", "status"])

@vpn_connection.command("create")
@click.argument("name")
@click.option("--router", "router_id", required=True)
@click.option("--peer-address", required=True)
@click.option("--peer-id", required=True)
@click.option("--peer-cidr", "peer_cidrs", multiple=True, required=True)
@click.option("--psk", required=True)
@click.option("--ikepolicy", "ikepolicy_id", required=True)
@click.option("--ipsecpolicy", "ipsecpolicy_id", required=True)
@click.pass_context
def vpn_connection_create(ctx, name, router_id, peer_address, peer_id, peer_cidrs, psk, ikepolicy_id, ipsecpolicy_id):
    body = {
        "name": name,
        "router_id": router_id,
        "peer_address": peer_address,
        "peer_id": peer_id,
        "peer_cidrs": list(peer_cidrs),
        "psk": psk,
        "ikepolicy_id": ikepolicy_id,
        "ipsecpolicy_id": ipsecpolicy_id,
    }
    data = _api("post", f"{DEFAULT_URLS['network']}/vpn/site-connections", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])

@vpn_connection.command("show")
@click.argument("conn_id")
@click.pass_context
def vpn_connection_show(ctx, conn_id):
    data = _api("get", f"{DEFAULT_URLS['network']}/vpn/site-connections/{conn_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@vpn_connection.command("delete")
@click.argument("conn_id")
@click.pass_context
def vpn_connection_delete(ctx, conn_id):
    _api("delete", f"{DEFAULT_URLS['network']}/vpn/site-connections/{conn_id}", ctx.obj["profile"])
    click.echo(f"✓ VPN site connection {conn_id} deleted")

@vpn_connection.command("config")
@click.argument("conn_id")
@click.pass_context
def vpn_connection_config(ctx, conn_id):
    data = _api("post", f"{DEFAULT_URLS['network']}/vpn/site-connections/{conn_id}/config", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ── Managed Kubernetes Engine Commands ─────────────────────────────────────────

@cli.group("k8s-template")
@click.pass_context
def k8s_template(ctx):
    """Manage Kubernetes Cluster Templates"""

@k8s_template.command("list")
@click.pass_context
def k8s_template_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['k8s-engine']}/cluster-templates", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "k8s_version", "master_flavor", "worker_flavor", "image_id"])

@k8s_template.command("create")
@click.argument("name")
@click.option("--image", "image_id", required=True, help="Image ID for k8s nodes")
@click.option("--k8s-version", default="v1.30.2+k3s1")
@click.option("--master-flavor", default="m1.medium")
@click.option("--worker-flavor", default="m1.small")
@click.option("--network-driver", default="ovn")
@click.option("--volume-driver", default="cloudx-csi")
@click.pass_context
def k8s_template_create(ctx, name, image_id, k8s_version, master_flavor, worker_flavor, network_driver, volume_driver):
    body = {
        "name": name,
        "image_id": image_id,
        "k8s_version": k8s_version,
        "master_flavor": master_flavor,
        "worker_flavor": worker_flavor,
        "network_driver": network_driver,
        "volume_driver": volume_driver
    }
    data = _api("post", f"{DEFAULT_URLS['k8s-engine']}/cluster-templates", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])

@k8s_template.command("show")
@click.argument("template_id")
@click.pass_context
def k8s_template_show(ctx, template_id):
    data = _api("get", f"{DEFAULT_URLS['k8s-engine']}/cluster-templates/{template_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@k8s_template.command("delete")
@click.argument("template_id")
@click.pass_context
def k8s_template_delete(ctx, template_id):
    _api("delete", f"{DEFAULT_URLS['k8s-engine']}/cluster-templates/{template_id}", ctx.obj["profile"])
    click.echo(f"✓ Cluster Template {template_id} deleted")


@cli.group("cluster")
@click.pass_context
def k8s_cluster(ctx):
    """Manage Kubernetes Clusters"""

@k8s_cluster.command("list")
@click.pass_context
def k8s_cluster_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['k8s-engine']}/clusters", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "template_id", "master_count", "worker_count", "status"])

@k8s_cluster.command("create")
@click.argument("name")
@click.option("--template", "template_id", required=True, help="Cluster Template ID")
@click.option("--network", "network_id", required=True, help="Network ID")
@click.option("--masters", "master_count", default=1, type=int)
@click.option("--workers", "worker_count", default=2, type=int)
@click.pass_context
def k8s_cluster_create(ctx, name, template_id, network_id, master_count, worker_count):
    body = {
        "name": name,
        "template_id": template_id,
        "network_id": network_id,
        "master_count": master_count,
        "worker_count": worker_count
    }
    data = _api("post", f"{DEFAULT_URLS['k8s-engine']}/clusters", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])

@k8s_cluster.command("show")
@click.argument("cluster_id")
@click.pass_context
def k8s_cluster_show(ctx, cluster_id):
    data = _api("get", f"{DEFAULT_URLS['k8s-engine']}/clusters/{cluster_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])

@k8s_cluster.command("scale")
@click.argument("cluster_id")
@click.option("--workers", "worker_count", required=True, type=int)
@click.pass_context
def k8s_cluster_scale(ctx, cluster_id, worker_count):
    body = {"worker_count": worker_count}
    data = _api("post", f"{DEFAULT_URLS['k8s-engine']}/clusters/{cluster_id}/scale", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])

@k8s_cluster.command("kubeconfig")
@click.argument("cluster_id")
@click.pass_context
def k8s_cluster_kubeconfig(ctx, cluster_id):
    data = _api("get", f"{DEFAULT_URLS['k8s-engine']}/clusters/{cluster_id}/kubeconfig", ctx.obj["profile"])
    click.echo(data.get("kubeconfig", ""))

@k8s_cluster.command("delete")
@click.argument("cluster_id")
@click.pass_context
def k8s_cluster_delete(ctx, cluster_id):
    _api("delete", f"{DEFAULT_URLS['k8s-engine']}/clusters/{cluster_id}", ctx.obj["profile"])
    click.echo(f"✓ Kubernetes Cluster {cluster_id} deleted")


# ── Systemd Service Management Commands ────────────────────────────────────────

@cli.group("service")
@click.pass_context
def cloudx_service(ctx):
    """Manage CloudX systemd services & daemon lifecycle"""

@cloudx_service.command("list")
@click.pass_context
def cloudx_service_list(ctx):
    """List all installed CloudX systemd services"""
    res = os.system("systemctl list-units 'cloudx-*' --no-pager")
    if res != 0:
        click.echo("Systemd services list unavailable or not running on Linux systemd host.")

@cloudx_service.command("status")
@click.argument("service_name", required=False)
@click.pass_context
def cloudx_service_status(ctx, service_name):
    """Check status of a specific service or all CloudX services"""
    target = f"cloudx-{service_name}" if service_name and not service_name.startswith("cloudx-") else (service_name or "cloudx-*")
    os.system(f"systemctl status {target} --no-pager")

@cloudx_service.command("restart")
@click.argument("service_name")
@click.pass_context
def cloudx_service_restart(ctx, service_name):
    """Restart a CloudX systemd service"""
    target = f"cloudx-{service_name}" if not service_name.startswith("cloudx-") else service_name
    res = os.system(f"sudo systemctl restart {target}")
    if res == 0:
        click.echo(f"✓ Service {target} restarted")
    else:
        click.echo(f"❌ Failed to restart {target}")

@cloudx_service.command("logs")
@click.argument("service_name")
@click.option("--follow", "-f", is_flag=True, help="Follow journal output")
@click.option("--lines", "-n", default=50, help="Number of journal lines to show")
@click.pass_context
def cloudx_service_logs(ctx, service_name, follow, lines):
    """View journalctl logs for a CloudX service"""
    target = f"cloudx-{service_name}" if not service_name.startswith("cloudx-") else service_name
    flags = "-f" if follow else f"-n {lines}"
    os.system(f"journalctl -u {target} {flags} --no-pager")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cli()
