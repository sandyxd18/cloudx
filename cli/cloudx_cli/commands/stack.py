import json

import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output


def _template(path):
    with open(path, encoding="utf-8") as f:
        return f.read()  # the server parses JSON/YAML text itself


def _params(pairs):
    return dict(p.split("=", 1) for p in pairs)


@cli.group("stack")
@click.pass_context
def stack(ctx):
    """Manage orchestration stacks (Heat-like templates)"""


@stack.command("list")
@click.option("--status", default=None)
@click.pass_context
def stack_list(ctx, status):
    q = f"?status={status}" if status else ""
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/stacks{q}", ctx.obj["profile"])
    _output(data.get("stacks", data), ctx.obj["fmt"], ["id", "name", "status", "status_reason", "created_at"])


@stack.command("show")
@click.argument("ref")
@click.pass_context
def stack_show(ctx, ref):
    _output(_api("get", f"{DEFAULT_URLS['orchestration']}/stacks/{ref}", ctx.obj["profile"]), ctx.obj["fmt"])


@stack.command("create")
@click.argument("name")
@click.option("--template", "-t", "template", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--parameter", "-p", "params", multiple=True, metavar="KEY=VALUE")
@click.option("--no-rollback", is_flag=True)
@click.pass_context
def stack_create(ctx, name, template, params, no_rollback):
    payload = {"name": name, "template": _template(template), "parameters": _params(params), "disable_rollback": no_rollback}
    _output(_api("post", f"{DEFAULT_URLS['orchestration']}/stacks", ctx.obj["profile"], json=payload), ctx.obj["fmt"])


@stack.command("update")
@click.argument("ref")
@click.option("--template", "-t", "template", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--parameter", "-p", "params", multiple=True, metavar="KEY=VALUE")
@click.pass_context
def stack_update(ctx, ref, template, params):
    payload = {"template": _template(template), "parameters": _params(params)}
    _api("put", f"{DEFAULT_URLS['orchestration']}/stacks/{ref}", ctx.obj["profile"], json=payload)
    click.echo(f"Stack {ref} update started")


@stack.command("delete")
@click.argument("ref")
@click.pass_context
def stack_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['orchestration']}/stacks/{ref}", ctx.obj["profile"])
    click.echo(f"Stack {ref} delete started")


@stack.command("resources")
@click.argument("ref")
@click.pass_context
def stack_resources(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/stacks/{ref}/resources", ctx.obj["profile"])
    _output(data.get("resources", data), ctx.obj["fmt"], ["name", "type", "status", "physical_id"])


@stack.command("events")
@click.argument("ref")
@click.pass_context
def stack_events(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/stacks/{ref}/events", ctx.obj["profile"])
    _output(data.get("events", data), ctx.obj["fmt"], ["time", "resource", "status", "reason"])


@stack.command("outputs")
@click.argument("ref")
@click.pass_context
def stack_outputs(ctx, ref):
    click.echo(json.dumps(_api("get", f"{DEFAULT_URLS['orchestration']}/stacks/{ref}/outputs", ctx.obj["profile"]), indent=2))


@stack.command("suspend")
@click.argument("ref")
@click.pass_context
def stack_suspend(ctx, ref):
    _api("post", f"{DEFAULT_URLS['orchestration']}/stacks/{ref}/actions", ctx.obj["profile"], json={"suspend": None})
    click.echo(f"Stack {ref} suspend started")


@stack.command("resume")
@click.argument("ref")
@click.pass_context
def stack_resume(ctx, ref):
    _api("post", f"{DEFAULT_URLS['orchestration']}/stacks/{ref}/actions", ctx.obj["profile"], json={"resume": None})
    click.echo(f"Stack {ref} resume started")