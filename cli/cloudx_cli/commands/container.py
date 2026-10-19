import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output

def _u(path: str) -> str:
    return f"{DEFAULT_URLS['container']}{path}"

@cli.group("container")
def container():
    """Single containers with a tenant IP (Zun-like)"""

@container.command("list")
@click.pass_context
def container_list(ctx):
    _output(_api("get", _u("/containers"), ctx.obj["profile"])["items"], ctx.obj["fmt"], ["id", "name", "image", "ip", "status", "host"])

@container.command("show")
@click.argument("ref")
@click.pass_context
def container_show(ctx, ref):
    _output(_api("get", _u(f"/containers/{ref}"), ctx.obj["profile"]), ctx.obj["fmt"])

@container.command("create")
@click.argument("name")
@click.option("--image", required=True)
@click.option("--network", "network_id", required=True, help="Tenant network id")
@click.option("--subnet", "subnet_id", default=None)
@click.option("--command", "command", multiple=True, help="One argument per --command")
@click.option("--env", "env", multiple=True, help="KEY=VALUE")
@click.option("--cpu", type=float, default=0.5)
@click.option("--memory", "memory_mb", type=int, default=128, help="MB")
@click.option("--security-group", "security_group_ids", multiple=True)
@click.pass_context
def container_create(ctx, name, image, network_id, subnet_id, command, env, cpu, memory_mb, security_group_ids):
    body = {"name": name, "image": image, "network_id": network_id, "cpu": cpu, "memory_mb": memory_mb,
            "command": list(command), "env": dict(e.split("=", 1) for e in env),
            "security_group_ids": list(security_group_ids), **({"subnet_id": subnet_id} if subnet_id else {})}
    _output(_api("post", _u("/containers"), ctx.obj["profile"], json=body), ctx.obj["fmt"])

def _action(name):
    @container.command(name)
    @click.argument("ref")
    @click.pass_context
    def cmd(ctx, ref):
        _output(_api("post", _u(f"/containers/{ref}/{name}"), ctx.obj["profile"]), ctx.obj["fmt"])
    return cmd

for _n in ("start", "stop", "restart"):
    _action(_n)

@container.command("logs")
@click.argument("ref")
@click.pass_context
def container_logs(ctx, ref):
    click.echo(_api("get", _u(f"/containers/{ref}/logs"), ctx.obj["profile"])["logs"])

@container.command("delete")
@click.argument("ref")
@click.pass_context
def container_delete(ctx, ref):
    _api("delete", _u(f"/containers/{ref}"), ctx.obj["profile"])