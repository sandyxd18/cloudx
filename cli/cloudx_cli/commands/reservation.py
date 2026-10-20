import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output

def _u(path: str) -> str:
    return f"{DEFAULT_URLS['reservation']}{path}"

@cli.group("reservation")
def reservation():
    """Capacity leases (Blazar-like)"""

@reservation.command("list")
@click.pass_context
def reservation_list(ctx):
    _output(_api("get", _u("/leases"), ctx.obj["profile"])["items"], ctx.obj["fmt"], ["id", "name", "flavor", "amount", "start_at", "end_at", "status"])

@reservation.command("show")
@click.argument("ref")
@click.pass_context
def reservation_show(ctx, ref):
    _output(_api("get", _u(f"/leases/{ref}"), ctx.obj["profile"]), ctx.obj["fmt"])

@reservation.command("create")
@click.argument("name")
@click.option("--flavor", required=True)
@click.option("--amount", type=int, default=1)
@click.option("--start", "start_at", required=True, help="ISO time, UTC unless it carries an offset")
@click.option("--end", "end_at", required=True)
@click.pass_context
def reservation_create(ctx, name, flavor, amount, start_at, end_at):
    _output(_api("post", _u("/leases"), ctx.obj["profile"], json={"name": name, "flavor": flavor, "amount": amount, "start_at": start_at, "end_at": end_at}), ctx.obj["fmt"])

@reservation.command("delete")
@click.argument("ref")
@click.pass_context
def reservation_delete(ctx, ref):
    _api("delete", _u(f"/leases/{ref}"), ctx.obj["profile"])

@reservation.command("capacity")
@click.option("--start", "start_at", default=None)
@click.option("--end", "end_at", default=None)
@click.pass_context
def reservation_capacity(ctx, start_at, end_at):
    params = {k: v for k, v in (("start", start_at), ("end", end_at)) if v}
    _output(_api("get", _u("/capacity"), ctx.obj["profile"], params=params), ctx.obj["fmt"])
