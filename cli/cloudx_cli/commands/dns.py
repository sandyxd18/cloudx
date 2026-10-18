import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output


def _u(path: str) -> str:
    return f"{DEFAULT_URLS['dns']}{path}"


@cli.group("dns")
def dns():
    """Manage DNS zones, records and secondary servers"""


@dns.group("zone")
def zone():
    """DNS zones"""


@zone.command("list")
@click.pass_context
def zone_list(ctx):
    _output(_api("get", _u("/zones"), ctx.obj["profile"])["zones"], ctx.obj["fmt"], ["id", "name", "scope", "kind", "status", "serial"])


@zone.command("create")
@click.argument("name")
@click.option("--email", default=None)
@click.option("--ttl", type=int, default=300)
@click.option("--internal", is_flag=True, help="Private zone: served to tenant VMs only, never to the internet")
@click.pass_context
def zone_create(ctx, name, email, ttl, internal):
    body = {"name": name, "ttl": ttl, "scope": "internal" if internal else "public", **({"email": email} if email else {})}
    _output(_api("post", _u("/zones"), ctx.obj["profile"], json=body), ctx.obj["fmt"])


@zone.command("show")
@click.argument("ref")
@click.pass_context
def zone_show(ctx, ref):
    """Show a zone and the name servers to enter at your registrar"""
    data = _api("get", _u(f"/zones/{ref}"), ctx.obj["profile"])
    base = _api("get", _u("/zones"), ctx.obj["profile"])["zones"]
    root = min((z["name"] for z in base if z["kind"] == "platform"), key=len, default=None)
    _output(data, ctx.obj["fmt"])
    if data["kind"] == "user" and data["scope"] == "public" and root:
        click.echo(f"NS: ns1.{root}")


@zone.command("update")
@click.argument("ref")
@click.option("--email", default=None)
@click.option("--ttl", type=int, default=None)
@click.pass_context
def zone_update(ctx, ref, email, ttl):
    body = {k: v for k, v in (("email", email), ("ttl", ttl)) if v is not None}
    _output(_api("patch", _u(f"/zones/{ref}"), ctx.obj["profile"], json=body), ctx.obj["fmt"])


@zone.command("delete")
@click.argument("ref")
@click.pass_context
def zone_delete(ctx, ref):
    _api("delete", _u(f"/zones/{ref}"), ctx.obj["profile"])


@zone.command("export")
@click.argument("ref")
@click.pass_context
def zone_export(ctx, ref):
    """Print the zone as a BIND zone file"""
    z = _api("get", _u(f"/zones/{ref}"), ctx.obj["profile"])
    rs = _api("get", _u(f"/zones/{ref}/recordsets"), ctx.obj["profile"])["recordsets"]
    click.echo(f"$ORIGIN {z['name']}.\n$TTL {z['ttl']}")
    for r in rs:
        for v in r["records"]:
            click.echo(f"{r['name']}\t{r['ttl'] or ''}\tIN\t{r['type']}\t{v}")


@dns.group("record")
def record():
    """Recordsets"""


@record.command("list")
@click.argument("zone_ref")
@click.pass_context
def record_list(ctx, zone_ref):
    rows = [{**r, "records": ", ".join(r["records"])} for r in _api("get", _u(f"/zones/{zone_ref}/recordsets"), ctx.obj["profile"])["recordsets"]]
    _output(rows, ctx.obj["fmt"], ["id", "name", "type", "ttl", "records", "managed_by"])


@record.command("create")
@click.argument("zone_ref")
@click.argument("name")
@click.argument("rtype")
@click.argument("records", nargs=-1, required=True)
@click.option("--ttl", type=int, default=None)
@click.pass_context
def record_create(ctx, zone_ref, name, rtype, records, ttl):
    body = {"name": name, "type": rtype, "records": list(records), **({"ttl": ttl} if ttl else {})}
    _output(_api("post", _u(f"/zones/{zone_ref}/recordsets"), ctx.obj["profile"], json=body), ctx.obj["fmt"])


@record.command("set")
@click.argument("zone_ref")
@click.argument("record_id")
@click.argument("records", nargs=-1, required=True)
@click.option("--ttl", type=int, default=None)
@click.pass_context
def record_set(ctx, zone_ref, record_id, records, ttl):
    body = {"records": list(records), **({"ttl": ttl} if ttl else {})}
    _output(_api("put", _u(f"/zones/{zone_ref}/recordsets/{record_id}"), ctx.obj["profile"], json=body), ctx.obj["fmt"])


@record.command("delete")
@click.argument("zone_ref")
@click.argument("record_id")
@click.pass_context
def record_delete(ctx, zone_ref, record_id):
    _api("delete", _u(f"/zones/{zone_ref}/recordsets/{record_id}"), ctx.obj["profile"])


@dns.group("secondary")
def secondary():
    """Secondary name servers allowed to AXFR a public zone"""


@secondary.command("add")
@click.argument("zone_ref")
@click.argument("address")
@click.pass_context
def secondary_add(ctx, zone_ref, address):
    _output(_api("post", _u(f"/zones/{zone_ref}/secondaries"), ctx.obj["profile"], json={"address": address}), ctx.obj["fmt"])


@secondary.command("list")
@click.argument("zone_ref")
@click.pass_context
def secondary_list(ctx, zone_ref):
    _output(_api("get", _u(f"/zones/{zone_ref}/secondaries"), ctx.obj["profile"])["secondaries"], ctx.obj["fmt"], ["id", "address"])


@secondary.command("remove")
@click.argument("zone_ref")
@click.argument("secondary_id")
@click.pass_context
def secondary_remove(ctx, zone_ref, secondary_id):
    _api("delete", _u(f"/zones/{zone_ref}/secondaries/{secondary_id}"), ctx.obj["profile"])