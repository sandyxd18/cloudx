import json

import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output

def _u(path: str) -> str:
    return f"{DEFAULT_URLS['messaging']}{path}"

@cli.group("queue")
def queue():
    """Message queues (Zaqar-like)"""

@queue.command("list")
@click.pass_context
def queue_list(ctx):
    _output(_api("get", _u("/queues"), ctx.obj["profile"])["items"], ctx.obj["fmt"], ["id", "name", "project_id"])

@queue.command("create")
@click.argument("name")
@click.pass_context
def queue_create(ctx, name):
    _output(_api("put", _u(f"/queues/{name}"), ctx.obj["profile"]), ctx.obj["fmt"])

@queue.command("delete")
@click.argument("name")
@click.pass_context
def queue_delete(ctx, name):
    _api("delete", _u(f"/queues/{name}"), ctx.obj["profile"])

@queue.command("stats")
@click.argument("name")
@click.pass_context
def queue_stats(ctx, name):
    _output(_api("get", _u(f"/queues/{name}/stats"), ctx.obj["profile"]), ctx.obj["fmt"])

@queue.command("post")
@click.argument("name")
@click.argument("body")
@click.option("--ttl", type=int, default=3600, help="seconds, 60 to 1209600")
@click.pass_context
def queue_post(ctx, name, body, ttl):
    """BODY is JSON, or plain text if it does not parse."""
    try:
        body = json.loads(body)
    except ValueError:
        pass
    _output(_api("post", _u(f"/queues/{name}/messages"), ctx.obj["profile"], json={"messages": [{"body": body, "ttl": ttl}]}), ctx.obj["fmt"])

@queue.command("peek")
@click.argument("name")
@click.option("--limit", type=int, default=10)
@click.pass_context
def queue_peek(ctx, name, limit):
    _output(_api("get", _u(f"/queues/{name}/messages?limit={limit}"), ctx.obj["profile"])["items"], ctx.obj["fmt"], ["id", "body", "ttl", "deliveries"])

@queue.command("claim")
@click.argument("name")
@click.option("--ttl", type=int, default=300)
@click.option("--limit", type=int, default=10)
@click.pass_context
def queue_claim(ctx, name, ttl, limit):
    r = _api("post", _u(f"/queues/{name}/claims?ttl={ttl}&limit={limit}"), ctx.obj["profile"])
    click.echo(f"claim_id: {r['claim_id']}")
    _output(r["items"], ctx.obj["fmt"], ["id", "body", "deliveries"])

@queue.command("ack")
@click.argument("name")
@click.argument("message_id")
@click.option("--claim", "claim_id", default=None, help="claim_id from `queue claim`")
@click.pass_context
def queue_ack(ctx, name, message_id, claim_id):
    """Delete a message (after processing it)."""
    q = f"?claim_id={claim_id}" if claim_id else ""
    _api("delete", _u(f"/queues/{name}/messages/{message_id}{q}"), ctx.obj["profile"])
