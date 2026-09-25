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
    _output(data.get("buckets", data), ctx.obj["fmt"], ["id", "name", "created_at"])

@object_store.command("create")
@click.argument("name")
@click.option("--type-id", help="Bucket type ID (optional)")
@click.pass_context
def bucket_create(ctx, name, type_id):
    payload = {"name": name}
    if type_id: payload["bucket_type_id"] = type_id
    data = _api("post", f"{DEFAULT_URLS['object-store']}/buckets", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])

@object_store.command("delete")
@click.argument("name")
@click.pass_context
def bucket_delete(ctx, name):
    _api("delete", f"{DEFAULT_URLS['object-store']}/buckets/{name}", ctx.obj["profile"])
    click.echo(f"✓ Bucket {name} deleted")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cli()
