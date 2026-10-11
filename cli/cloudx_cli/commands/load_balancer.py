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
    if subnet:
        payload["vip_subnet_id"] = subnet
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/load-balancers", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@load_balancer.command("delete")
@click.argument("ref")
@click.pass_context
def lb_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Load balancer {ref} deleted")


# ── Load Balancer Certificates ───────────────────────────────────────────────
@load_balancer.group("certificate")
@click.pass_context
def lb_certificate(ctx):
    """Manage load balancer SSL/TLS certificates"""


def _read_pem_files(cert_path: str, key_path: str | None = None) -> tuple[str, str]:
    with open(cert_path, "r", encoding="utf-8") as f:
        cert_data = f.read()
    if key_path:
        with open(key_path, "r", encoding="utf-8") as f:
            key_data = f.read()
        return cert_data.strip(), key_data.strip()

    cert_lines = []
    key_lines = []
    in_key = False
    for line in cert_data.splitlines(keepends=True):
        if "BEGIN" in line and "KEY" in line:
            in_key = True
        if in_key:
            key_lines.append(line)
        else:
            cert_lines.append(line)
        if "END" in line and "KEY" in line:
            in_key = False

    cert_pem = "".join(cert_lines).strip()
    key_pem = "".join(key_lines).strip()
    if not key_pem:
        key_pem = cert_pem
    return cert_pem, key_pem


@lb_certificate.command("create")
@click.argument("lb_ref")
@click.option("--name", required=True, help="Certificate name")
@click.option("--cert", required=True, type=click.Path(exists=True), help="Certificate PEM file path")
@click.option("--key", default=None, type=click.Path(exists=True), help="Private key PEM file path")
@click.pass_context
def cert_create(ctx, lb_ref, name, cert, key):
    cert_pem, key_pem = _read_pem_files(cert, key)
    payload = {"name": name, "certificate_pem": cert_pem, "private_key_pem": key_pem}
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{lb_ref}/certificates", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@lb_certificate.command("list")
@click.argument("lb_ref")
@click.pass_context
def cert_list(ctx, lb_ref):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{lb_ref}/certificates", ctx.obj["profile"])
    _output(data.get("certificates", data.get("items", data)), ctx.obj["fmt"], ["id", "name", "created_at"])


@lb_certificate.command("delete")
@click.argument("lb_ref")
@click.argument("cert_ref")
@click.pass_context
def cert_delete(ctx, lb_ref, cert_ref):
    _api("delete", f"{DEFAULT_URLS['load-balancer']}/load-balancers/{lb_ref}/certificates/{cert_ref}", ctx.obj["profile"])
    click.echo(f"✓ Certificate {cert_ref} deleted")


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
@click.option("--protocol", type=click.Choice(["HTTP", "TCP", "UDP", "HTTPS"]), default="HTTP", help="Protocol")
@click.option("--certificate", "--certificate-id", "certificate_id", default=None, help="Certificate ID for HTTPS")
@click.pass_context
def listener_create(ctx, lb_ref, name, port, protocol, certificate_id):
    payload = {"name": name, "protocol": protocol, "protocol_port": port}
    if certificate_id:
        payload["certificate_id"] = certificate_id
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
    if server_group:
        payload["server_group_id"] = server_group
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


# ── L7 Policies ──────────────────────────────────────────────────────────────
@load_balancer.group("l7policy")
@click.pass_context
def lb_l7policy(ctx):
    """Manage L7 policies"""


@lb_l7policy.command("create")
@click.argument("listener_ref")
@click.option("--name", required=True, help="L7 policy name")
@click.option("--action", type=click.Choice(["REDIRECT_TO_POOL", "REJECT"]), default="REDIRECT_TO_POOL", help="Policy action")
@click.option("--redirect-pool", default=None, help="Target pool ID for REDIRECT_TO_POOL action")
@click.option("--position", type=int, default=1, help="Policy evaluation position")
@click.pass_context
def l7policy_create(ctx, listener_ref, name, action, redirect_pool, position):
    payload = {"name": name, "action": action, "position": position}
    if redirect_pool:
        payload["redirect_pool_id"] = redirect_pool
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/listeners/{listener_ref}/l7policies", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@lb_l7policy.command("list")
@click.argument("listener_ref")
@click.pass_context
def l7policy_list(ctx, listener_ref):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/listeners/{listener_ref}/l7policies", ctx.obj["profile"])
    _output(data.get("l7policies", data.get("items", data)), ctx.obj["fmt"], ["id", "name", "action", "position", "redirect_pool_id"])


@lb_l7policy.command("delete")
@click.argument("policy_ref")
@click.pass_context
def l7policy_delete(ctx, policy_ref):
    _api("delete", f"{DEFAULT_URLS['load-balancer']}/l7policies/{policy_ref}", ctx.obj["profile"])
    click.echo(f"✓ L7 policy {policy_ref} deleted")


# ── L7 Rules ─────────────────────────────────────────────────────────────────
@load_balancer.group("l7rule")
@click.pass_context
def lb_l7rule(ctx):
    """Manage L7 rules"""


@lb_l7rule.command("create")
@click.argument("policy_ref")
@click.option("--type", "type_", type=click.Choice(["PATH", "HOST_NAME"]), required=True, help="Match type")
@click.option("--compare-type", type=click.Choice(["STARTS_WITH", "ENDS_WITH", "EQUAL_TO"]), default="STARTS_WITH", help="Comparison type")
@click.option("--value", required=True, help="Match value")
@click.pass_context
def l7rule_create(ctx, policy_ref, type_, compare_type, value):
    payload = {"type": type_, "compare_type": compare_type, "value": value}
    data = _api("post", f"{DEFAULT_URLS['load-balancer']}/l7policies/{policy_ref}/rules", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@lb_l7rule.command("list")
@click.argument("policy_ref")
@click.pass_context
def l7rule_list(ctx, policy_ref):
    data = _api("get", f"{DEFAULT_URLS['load-balancer']}/l7policies/{policy_ref}/rules", ctx.obj["profile"])
    _output(data.get("rules", data.get("items", data)), ctx.obj["fmt"], ["id", "type", "compare_type", "value"])


@lb_l7rule.command("delete")
@click.argument("policy_ref")
@click.argument("rule_ref")
@click.pass_context
def l7rule_delete(ctx, policy_ref, rule_ref):
    _api("delete", f"{DEFAULT_URLS['load-balancer']}/l7policies/{policy_ref}/rules/{rule_ref}", ctx.obj["profile"])
    click.echo(f"✓ L7 rule {rule_ref} deleted")
