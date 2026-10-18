import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output

def _u(path: str) -> str:
    return f"{DEFAULT_URLS['file_store']}{path}"

@cli.group("file-store")
def file_store():
    """Manage NFS shares (Manila-like)"""

@file_store.group("network")
def network():
    """Share networks: the tenant-network port a share is mounted from"""

@network.command("list")
@click.pass_context
def network_list(ctx):
    _output(_api("get", _u("/share-networks"), ctx.obj["profile"])["items"], ctx.obj["fmt"], ["id", "name", "ip", "status"])

@network.command("create")
@click.argument("name")
@click.option("--network", "network_id", required=True, help="Tenant network id")
@click.option("--subnet", "subnet_id", default=None)
@click.pass_context
def network_create(ctx, name, network_id, subnet_id):
    body = {"name": name, "network_id": network_id, **({"subnet_id": subnet_id} if subnet_id else {})}
    _output(_api("post", _u("/share-networks"), ctx.obj["profile"], json=body), ctx.obj["fmt"])

@network.command("delete")
@click.argument("ref")
@click.pass_context
def network_delete(ctx, ref):
    _api("delete", _u(f"/share-networks/{ref}"), ctx.obj["profile"])

@file_store.group("share")
def share():
    """NFS shares"""

@share.command("list")
@click.pass_context
def share_list(ctx):
    _output(_api("get", _u("/shares"), ctx.obj["profile"])["items"], ctx.obj["fmt"], ["id", "name", "size_gb", "status", "export_location"])

@share.command("show")
@click.argument("ref")
@click.pass_context
def share_show(ctx, ref):
    _output(_api("get", _u(f"/shares/{ref}"), ctx.obj["profile"]), ctx.obj["fmt"])

@share.command("create")
@click.argument("name")
@click.option("--size", "size_gb", type=int, required=True, help="GB")
@click.option("--network", "share_network_id", required=True, help="Share network id")
@click.pass_context
def share_create(ctx, name, size_gb, share_network_id):
    body = {"name": name, "size_gb": size_gb, "share_network_id": share_network_id}
    _output(_api("post", _u("/shares"), ctx.obj["profile"], json=body), ctx.obj["fmt"])

@share.command("extend")
@click.argument("ref")
@click.argument("size_gb", type=int)
@click.pass_context
def share_extend(ctx, ref, size_gb):
    _output(_api("post", _u(f"/shares/{ref}/extend"), ctx.obj["profile"], json={"size_gb": size_gb}), ctx.obj["fmt"])

@share.command("delete")
@click.argument("ref")
@click.pass_context
def share_delete(ctx, ref):
    _api("delete", _u(f"/shares/{ref}"), ctx.obj["profile"])

@file_store.group("access")
def access():
    """Who may mount a share (no rule means nobody)"""

@access.command("list")
@click.argument("share")
@click.pass_context
def access_list(ctx, share):
    _output(_api("get", _u(f"/shares/{share}/access"), ctx.obj["profile"])["items"], ctx.obj["fmt"], ["id", "to", "level"])

@access.command("add")
@click.argument("share")
@click.argument("to")
@click.option("--level", type=click.Choice(["rw", "ro"]), default="rw")
@click.pass_context
def access_add(ctx, share, to, level):
    _output(_api("post", _u(f"/shares/{share}/access"), ctx.obj["profile"], json={"to": to, "level": level}), ctx.obj["fmt"])

@access.command("remove")
@click.argument("share")
@click.argument("rule")
@click.pass_context
def access_remove(ctx, share, rule):
    _api("delete", _u(f"/shares/{share}/access/{rule}"), ctx.obj["profile"])
