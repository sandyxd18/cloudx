import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output


# ═══════════════════════════════════════════════════════════════════════════════
# LOAD BALANCER
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("load-balancer")
@click.pass_context
def load_balancer(ctx):
    """Manage load balancers (NLB L4 & ALB L7)"""


@load_balancer.command("list")
@click.pass_context
def lb_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/load-balancers", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"],
            ["id", "name", "lb_type", "vip_address", "provisioning_status", "operating_status"])


@load_balancer.command("show")
@click.argument("ref")
@click.pass_context
def lb_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@load_balancer.command("create")
@click.argument("name")
@click.option("--network", required=True, help="Network name or ID")
@click.option("--subnet", default=None, help="Subnet name or ID")
@click.option("--type", "lb_type", type=click.Choice(["nlb", "alb"]), default="nlb")
@click.pass_context
def lb_create(ctx, name, network, subnet, lb_type):
    payload = {"name": name, "vip_network_id": network, "lb_type": lb_type}
    if subnet: payload["vip_subnet_id"] = subnet
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/load-balancers", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@load_balancer.command("delete")
@click.argument("ref")
@click.pass_context
def lb_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Load balancer {ref} deleted")


# ── Load Balancer Listeners ───────────────────────────────────────────────────
@load_balancer.group("listener")
@click.pass_context
def lb_listener(ctx):
    """Manage load balancer listeners"""


@lb_listener.command("list")
@click.argument("lb_ref")
@click.pass_context
def listener_list(ctx, lb_ref):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{lb_ref}/listeners", ctx.obj["profile"])
    _output(data.get("listeners", data.get("items", data)), ctx.obj["fmt"], ["id", "name", "protocol", "protocol_port", "default_pool_id"])


@lb_listener.command("create")
@click.argument("lb_ref")
@click.option("--name", required=True, help="Listener name")
@click.option("--port", required=True, type=int, help="Protocol port (e.g. 80, 443)")
@click.option("--protocol", type=click.Choice(["HTTP", "TCP", "UDP"]), default="HTTP", help="Protocol")
@click.pass_context
def listener_create(ctx, lb_ref, name, port, protocol):
    payload = {"name": name, "protocol": protocol, "protocol_port": port}
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{lb_ref}/listeners", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


# ── Load Balancer Pools ───────────────────────────────────────────────────────
@load_balancer.group("pool")
@click.pass_context
def lb_pool(ctx):
    """Manage load balancer pools and members"""


@lb_pool.command("create")
@click.argument("listener_ref")
@click.option("--name", required=True, help="Pool name")
@click.option("--algorithm", type=click.Choice(["ROUND_ROBIN", "LEAST_CONNECTIONS", "SOURCE_IP"]), default="ROUND_ROBIN")
@click.option("--server-group", default=None, help="Server Group ID to auto-sync members")
@click.pass_context
def pool_create(ctx, listener_ref, name, algorithm, server_group):
    payload = {"name": name, "lb_algorithm": algorithm}
    if server_group: payload["server_group_id"] = server_group
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/listeners/{listener_ref}/pools", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@lb_pool.command("show")
@click.argument("pool_ref")
@click.pass_context
def pool_show(ctx, pool_ref):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/pools/{pool_ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@lb_pool.command("sync")
@click.argument("pool_ref")
@click.pass_context
def pool_sync(ctx, pool_ref):
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/pools/{pool_ref}/sync", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ── Load Balancer Health Monitors ──────────────────────────────────────────────
@load_balancer.group("monitor")
@click.pass_context
def lb_monitor(ctx):
    """Manage health monitors for pools"""


@lb_monitor.command("create")
@click.argument("pool_ref")
@click.option("--type", "type_", type=click.Choice(["HTTP", "TCP", "PING"]), default="HTTP", help="Monitor type")
@click.option("--delay", default=5, type=int, help="Check interval in seconds")
@click.option("--timeout", default=5, type=int, help="Timeout in seconds")
@click.option("--max-retries", default=3, type=int, help="Max retries before offline")
@click.option("--url-path", default="/", help="URL path for HTTP monitor")
@click.pass_context
def monitor_create(ctx, pool_ref, type_, delay, timeout, max_retries, url_path):
    payload = {
        "type": type_,
        "delay": delay,
        "timeout": timeout,
        "max_retries": max_retries,
        "url_path": url_path,
    }
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/pools/{pool_ref}/health-monitors", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])
