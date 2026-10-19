import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output

def _u(path: str) -> str:
    return f"{DEFAULT_URLS['ha']}{path}"

@cli.group("ha")
def ha():
    """Instance high availability: host monitor, iSCSI fencing, evacuate (Masakari-like)"""

@ha.command("hosts")
@click.pass_context
def ha_hosts(ctx):
    _output(_api("get", _u("/hosts"), ctx.obj["profile"])["items"], ctx.obj["fmt"], ["host", "state", "last_seen", "fails", "has_iqn"])

@ha.command("reset")
@click.argument("host")
@click.pass_context
def ha_reset(ctx, host):
    """Return a fenced host to service (run after forced_down=false)"""
    _output(_api("post", _u(f"/hosts/{host}/reset"), ctx.obj["profile"]), ctx.obj["fmt"])

@ha.command("protected")
@click.pass_context
def ha_protected(ctx):
    _output(_api("get", _u("/protected"), ctx.obj["profile"])["items"], ctx.obj["fmt"], ["server_id", "project_id", "created_at"])

@ha.command("protect")
@click.argument("server")
@click.pass_context
def ha_protect(ctx, server):
    _output(_api("put", _u(f"/protected/{server}"), ctx.obj["profile"]), ctx.obj["fmt"])

@ha.command("unprotect")
@click.argument("server")
@click.pass_context
def ha_unprotect(ctx, server):
    _api("delete", _u(f"/protected/{server}"), ctx.obj["profile"])

@ha.command("recoveries")
@click.pass_context
def ha_recoveries(ctx):
    _output(_api("get", _u("/recoveries"), ctx.obj["profile"])["items"], ctx.obj["fmt"], ["host", "server_id", "status", "message", "started_at"])