import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION / AUTO-SCALING
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("scaling-group")
@click.pass_context
def scaling_group(ctx):
    """Auto-scaling group orchestration"""


@scaling_group.command("list")
@click.pass_context
def scaling_group_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "min_instances", "max_instances", "target_cpu_percent", "status"])


@scaling_group.command("show")
@click.argument("group_id")
@click.pass_context
def scaling_group_show(ctx, group_id):
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@scaling_group.command("create")
@click.argument("name")
@click.option("--image", "image_id", required=True)
@click.option("--flavor", "flavor_id", required=True)
@click.option("--network", "network_id", required=True)
@click.option("--subnet", "subnet_id")
@click.option("--min-instances", type=int, default=1)
@click.option("--max-instances", type=int, default=5)
@click.option("--target-cpu", "target_cpu_percent", type=float, default=70.0)
@click.option("--cooldown", "cooldown_seconds", type=int, default=300)
@click.option("--scale-in-cooldown", "scale_in_cooldown_seconds", type=int, default=600)
@click.option("--description", default=None)
@click.pass_context
def scaling_group_create(ctx, name, image_id, flavor_id, network_id, subnet_id, min_instances,
                         max_instances, target_cpu_percent, cooldown_seconds, scale_in_cooldown_seconds, description):
    payload = {
        "name": name,
        "image_id": image_id,
        "flavor_id": flavor_id,
        "network_id": network_id,
        "min_instances": min_instances,
        "max_instances": max_instances,
        "target_cpu_percent": target_cpu_percent,
        "cooldown_seconds": cooldown_seconds,
        "scale_in_cooldown_seconds": scale_in_cooldown_seconds,
    }
    if subnet_id:   payload["subnet_id"] = subnet_id
    if description: payload["description"] = description
    data = _api("post", f"{DEFAULT_URLS['orchestration']}/scaling-groups", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@scaling_group.command("update")
@click.argument("group_id")
@click.option("--min-instances", type=int, default=None)
@click.option("--max-instances", type=int, default=None)
@click.option("--target-cpu", "target_cpu_percent", type=float, default=None)
@click.option("--cooldown", "cooldown_seconds", type=int, default=None)
@click.pass_context
def scaling_group_update(ctx, group_id, min_instances, max_instances, target_cpu_percent, cooldown_seconds):
    payload = {}
    if min_instances is not None:      payload["min_instances"] = min_instances
    if max_instances is not None:      payload["max_instances"] = max_instances
    if target_cpu_percent is not None: payload["target_cpu_percent"] = target_cpu_percent
    if cooldown_seconds is not None:   payload["cooldown_seconds"] = cooldown_seconds
    if not payload:
        click.echo("Nothing to update.")
        return
    data = _api("patch", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@scaling_group.command("delete")
@click.argument("group_id")
@click.pass_context
def scaling_group_delete(ctx, group_id):
    _api("delete", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}", ctx.obj["profile"])
    click.echo(f"✓ Scaling group {group_id} deleted")


@scaling_group.command("scale-out")
@click.argument("group_id")
@click.pass_context
def scaling_group_scale_out(ctx, group_id):
    data = _api("post", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}/scale-out", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@scaling_group.command("scale-in")
@click.argument("group_id")
@click.pass_context
def scaling_group_scale_in(ctx, group_id):
    data = _api("post", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}/scale-in", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@scaling_group.command("instance-list")
@click.argument("group_id")
@click.pass_context
def scaling_group_instance_list(ctx, group_id):
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}/instances", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "server_id", "server_name", "status", "created_at"])


@scaling_group.command("history")
@click.argument("group_id")
@click.pass_context
def scaling_group_history(ctx, group_id):
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{group_id}/history", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "action", "reason", "server_id", "avg_cpu_before", "instance_count_after", "created_at"])


@cli.group("stack")
@click.pass_context
def stack_group(ctx):
    """Orchestration stack and resource management"""


@stack_group.command("list")
@click.pass_context
def stack_list(ctx):
    """List orchestration stacks / auto-scaling groups"""
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "min_instances", "max_instances", "status"])


@stack_group.command("show")
@click.argument("ref")
@click.pass_context
def stack_show(ctx, ref):
    """Show details of an orchestration stack"""
    data = _api("get", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@stack_group.command("delete")
@click.argument("ref")
@click.pass_context
def stack_delete(ctx, ref):
    """Delete an orchestration stack"""
    _api("delete", f"{DEFAULT_URLS['orchestration']}/scaling-groups/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Orchestration stack {ref} deleted")
