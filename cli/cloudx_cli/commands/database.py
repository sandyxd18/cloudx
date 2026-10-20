import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output

def _u(path: str) -> str:
    return f"{DEFAULT_URLS['database']}{path}"

@cli.group("database")
def database():
    """Database instances: PostgreSQL or MariaDB on a VM or in a container (Trove/RDS-like)"""

@database.command("list")
@click.pass_context
def database_list(ctx):
    _output(_api("get", _u("/instances"), ctx.obj["profile"])["items"], ctx.obj["fmt"],
            ["id", "name", "engine", "backend", "storage", "host", "port", "status"])

@database.command("show")
@click.argument("ref")
@click.pass_context
def database_show(ctx, ref):
    _output(_api("get", _u(f"/instances/{ref}"), ctx.obj["profile"]), ctx.obj["fmt"])

@database.command("create")
@click.argument("name")
@click.option("--engine", type=click.Choice(["postgresql", "mariadb"]), required=True)
@click.option("--backend", type=click.Choice(["vm", "container"]), required=True)
@click.option("--network", "network_id", required=True, help="Tenant network id")
@click.option("--storage", type=click.Choice(["local", "block", "file"]), default="local", help="block and file need --backend vm")
@click.option("--size", "size_gb", type=int, default=None, help="GB, for block and file storage")
@click.option("--image", default=None, help="VM only: Ubuntu cloud image")
@click.option("--flavor", default=None, help="VM only")
@click.option("--cpu", type=float, default=None, help="Container only")
@click.option("--memory", "memory_mb", type=int, default=None, help="Container only, MB")
@click.option("--allowed-cidr", default=None, help="Who may connect (default: the network's subnet)")
@click.option("--password", default=None, help="Generated if omitted; shown once")
@click.pass_context
def database_create(ctx, name, **kw):
    body = {"name": name, **{k: v for k, v in kw.items() if v is not None}}
    _output(_api("post", _u("/instances"), ctx.obj["profile"], json=body), ctx.obj["fmt"])

def _action(name):
    @database.command(name)
    @click.argument("ref")
    @click.pass_context
    def cmd(ctx, ref):
        _output(_api("post", _u(f"/instances/{ref}/{name}"), ctx.obj["profile"]), ctx.obj["fmt"])
    return cmd

for _n in ("start", "stop"):
    _action(_n)

@database.command("delete")
@click.argument("ref")
@click.pass_context
def database_delete(ctx, ref):
    _api("delete", _u(f"/instances/{ref}"), ctx.obj["profile"])

@database.group("backup")
def backup():
    """Backups of a VM + block database (crash-consistent copy of the data volume)"""

@backup.command("create")
@click.argument("instance")
@click.option("--name", default=None)
@click.pass_context
def backup_create(ctx, instance, name):
    _output(_api("post", _u(f"/instances/{instance}/backups"), ctx.obj["profile"], json={"name": name}), ctx.obj["fmt"])

@backup.command("list")
@click.pass_context
def backup_list(ctx):
    _output(_api("get", _u("/backups"), ctx.obj["profile"])["items"], ctx.obj["fmt"],
            ["id", "name", "engine", "size_gb", "status", "instance_id"])

@backup.command("delete")
@click.argument("ref")
@click.pass_context
def backup_delete(ctx, ref):
    _api("delete", _u(f"/backups/{ref}"), ctx.obj["profile"])

@backup.command("restore")
@click.argument("ref")
@click.argument("name")
@click.option("--network", "network_id", required=True, help="Tenant network id")
@click.option("--image", required=True, help="Same Ubuntu image (same engine major version) as the original")
@click.option("--flavor", required=True)
@click.option("--allowed-cidr", default=None)
@click.option("--password", default=None, help="Generated if omitted; shown once")
@click.pass_context
def backup_restore(ctx, ref, name, **kw):
    body = {"name": name, **{k: v for k, v in kw.items() if v is not None}}
    _output(_api("post", _u(f"/backups/{ref}/restore"), ctx.obj["profile"], json=body), ctx.obj["fmt"])
