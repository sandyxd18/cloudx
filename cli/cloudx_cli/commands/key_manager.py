import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output


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
