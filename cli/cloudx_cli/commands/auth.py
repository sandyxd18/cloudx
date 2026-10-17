import configparser
import sys

import click
import requests

from cloudx_cli.app import cli
from cloudx_cli.common import (
    CLOUDX_DIR,
    CONFIG_FILE,
    CREDS_FILE,
    DEFAULT_URLS,
    _api,
    _load_config,
    _load_creds,
    _output,
)


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


@auth.command("login")
@click.option("--username", "-u", default=None, help="User name to authenticate")
@click.option("--password", "-p", default=None, help="Password")
@click.option("--project", default="admin", help="Default project scope")
@click.option("--auth-url", default=None, help="Auth API endpoint URL")
@click.pass_context
def auth_login(ctx, username, password, project, auth_url):
    """Authenticate with username + password and configure CLI credentials"""
    click.echo("☁  cloudx auth login\n")
    target_auth_url = auth_url or DEFAULT_URLS["auth"]

    if not username:
        username = click.prompt("Username", default="admin")
    if not password:
        password = click.prompt("Password", hide_input=True)

    click.echo("Authenticating...", nl=False)
    try:
        r = requests.post(
            f"{target_auth_url}/auth/tokens",
            json={"username": username, "password": password},
            timeout=5,
        )
        if r.status_code != 201:
            click.echo(f" ✗\n❌ Login failed: invalid credentials (HTTP {r.status_code})", err=True)
            sys.exit(1)
        click.echo(" ✓")
        token_data = r.json()
        token = token_data["token"]

        CLOUDX_DIR.mkdir(parents=True, exist_ok=True)

        # Generate a CLI access key for persistent access
        ak_resp = requests.post(
            f"{target_auth_url}/access-keys",
            headers={"X-Auth-Token": token, "Content-Type": "application/json"},
            json={"name": f"cli-{username}"},
            timeout=5,
        )

        crd = configparser.ConfigParser()
        if CREDS_FILE.exists():
            crd.read(CREDS_FILE)
        if not crd.has_section("default"):
            crd["default"] = {}

        if ak_resp.status_code == 201:
            ak_data = ak_resp.json()
            crd["default"]["access_key_id"] = ak_data["access_key_id"]
            crd["default"]["secret_access_key"] = ak_data["secret_access_key"]
        else:
            # Fallback to direct token if access key creation is not supported
            crd["default"]["token"] = token

        with open(CREDS_FILE, "w") as f:
            crd.write(f)
        CREDS_FILE.chmod(0o600)

        cfg = configparser.ConfigParser()
        if CONFIG_FILE.exists():
            cfg.read(CONFIG_FILE)
        if not cfg.has_section("default"):
            cfg["default"] = {}
        cfg["default"]["auth_url"] = target_auth_url
        cfg["default"]["project"] = project
        cfg["default"]["output"] = "table"
        with open(CONFIG_FILE, "w") as f:
            cfg.write(f)
        CONFIG_FILE.chmod(0o600)

        click.echo(f"\n✓ Successfully logged in as '{username}' in project '{project}'")
        click.echo(f"✓ Credentials saved to {CREDS_FILE}")
        click.echo(f"✓ Configuration saved to {CONFIG_FILE}")
    except Exception as e:
        click.echo(f"\n❌ Login error: {e}", err=True)
        sys.exit(1)


# Project subcommands
@auth.group("project")
def auth_project():
    """Manage projects"""


@auth_project.command("create")
@click.argument("name")
@click.option("--description", "-d", default="")
@click.option("--domain", default=None, help="Domain id (system admin only; a domain admin always uses its own)")
@click.pass_context
def project_create(ctx, name, description, domain):
    data = _api("post", f"{DEFAULT_URLS['auth']}/projects",
                ctx.obj["profile"], json={"name": name, "description": description, "domain_id": domain})
    _output(data, ctx.obj["fmt"])


@auth_project.command("list")
@click.pass_context
def project_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['auth']}/projects", ctx.obj["profile"])
    _output(data["items"], ctx.obj["fmt"], ["id", "name", "domain_id", "description", "enabled"])


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
@click.option("--role", type=click.Choice(["admin", "member", "reader"]), default="member")
@click.option("--domain-admin", is_flag=True, help="Make the user an admin of its domain (system admin only)")
@click.pass_context
def user_create(ctx, username, email, password, project, role, domain_admin):
    data = _api("post", f"{DEFAULT_URLS['auth']}/users", ctx.obj["profile"],
                json={"username": username, "email": email, "password": password,
                      "project_id": project, "role": role, "is_domain_admin": domain_admin})
    _output(data, ctx.obj["fmt"])


@auth_user.command("list")
@click.pass_context
def user_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['auth']}/users", ctx.obj["profile"])
    _output(data["items"], ctx.obj["fmt"], ["id", "username", "email", "role", "domain_id", "is_domain_admin", "enabled"])


@auth_user.command("show")
@click.argument("ref")
@click.pass_context
def user_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['auth']}/users/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@auth_user.command("set")
@click.argument("ref")
@click.option("--role", type=click.Choice(["admin", "member", "reader"]))
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
