import json
import sys

import click
import requests

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _get_token, _output


# ═══════════════════════════════════════════════════════════════════════════════
# TELEMETRY
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("telemetry")
@click.pass_context
def telemetry(ctx):
    """Observability metrics & Prometheus export"""


@telemetry.command("metric-type-list")
@click.pass_context
def telemetry_metric_type_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/metric-types", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["name", "unit", "description", "default_enabled"])


@telemetry.command("metric-list")
@click.option("--type", "metric_type")
@click.option("--instance", "instance_id")
@click.option("--limit", type=int, default=50)
@click.pass_context
def telemetry_metric_list(ctx, metric_type, instance_id, limit):
    params = f"?limit={limit}"
    if metric_type:  params += f"&metric_type={metric_type}"
    if instance_id:  params += f"&instance_id={instance_id}"
    data = _api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/metrics{params}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "instance_id", "metric_type", "value", "unit", "timestamp"])


@telemetry.command("metric-create")
@click.option("--instance", "instance_id", required=True)
@click.option("--type", "metric_type", required=True)
@click.option("--value", type=float, required=True)
@click.option("--unit", default=None)
@click.option("--host", default=None)
@click.pass_context
def telemetry_metric_create(ctx, instance_id, metric_type, value, unit, host):
    payload = {"instance_id": instance_id, "metric_type": metric_type, "value": value}
    if unit: payload["unit"] = unit
    if host: payload["host"] = host
    data = _api("post", f"{DEFAULT_URLS['telemetry']}/telemetry/metrics", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@telemetry.command("stats")
@click.pass_context
def telemetry_stats(ctx):
    data = _api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/stats", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@telemetry.command("server-stats")
@click.argument("instance_id")
@click.pass_context
def telemetry_server_stats(ctx, instance_id):
    data = _api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/instances/{instance_id}/stats", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@telemetry.command("prometheus")
@click.pass_context
def telemetry_prometheus(ctx):
    """Dump Prometheus exposition format to stdout"""
    token = _get_token(ctx.obj["profile"])
    headers = {"X-Auth-Token": token} if token else {}
    r = requests.get(f"{DEFAULT_URLS['telemetry']}/metrics", headers=headers, timeout=30)
    if not r.ok:
        click.echo(f"❌ Prometheus export failed (HTTP {r.status_code}): {r.text}", err=True)
        sys.exit(1)
    click.echo(r.text)


# ═══════════════════════════════════════════════════════════════════════════════
# BILLING
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("billing")
@click.pass_context
def billing(ctx):
    """Usage rating, invoices, and PDF generation"""


@billing.command("rating-sheet-list")
@click.pass_context
def billing_rating_sheet_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['billing']}/billing/rating-sheets", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "resource_type", "unit", "rate_per_unit", "currency", "is_active"])


@billing.command("rating-sheet-create")
@click.option("--resource-type", required=True)
@click.option("--unit", default="hour")
@click.option("--rate-per-unit", type=float, required=True)
@click.option("--currency", default="IDR")
@click.option("--description", default=None)
@click.pass_context
def billing_rating_sheet_create(ctx, resource_type, unit, rate_per_unit, currency, description):
    payload = {"resource_type": resource_type, "unit": unit, "rate_per_unit": rate_per_unit,
               "currency": currency}
    if description: payload["description"] = description
    data = _api("post", f"{DEFAULT_URLS['billing']}/billing/rating-sheets", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@billing.command("usage-list")
@click.option("--project")
@click.option("--limit", type=int, default=50)
@click.pass_context
def billing_usage_list(ctx, project, limit):
    params = f"?limit={limit}"
    if project: params += f"&project_id={project}"
    data = _api("get", f"{DEFAULT_URLS['billing']}/billing/usage-records{params}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "project_id", "resource_type", "resource_id", "quantity", "unit", "total_cost"])


@billing.command("usage-create")
@click.option("--project", "project_id", required=True)
@click.option("--resource-type", required=True)
@click.option("--resource-id", default=None)
@click.option("--quantity", type=float, required=True)
@click.option("--unit", default="hour")
@click.option("--rate-per-unit", type=float, required=True)
@click.option("--period-start", required=True)
@click.option("--period-end", required=True)
@click.pass_context
def billing_usage_create(ctx, project_id, resource_type, resource_id, quantity, unit, rate_per_unit, period_start, period_end):
    payload = {
        "project_id": project_id,
        "resource_type": resource_type,
        "quantity": quantity,
        "unit": unit,
        "rate_per_unit": rate_per_unit,
        "period_start": period_start,
        "period_end": period_end,
    }
    if resource_id: payload["resource_id"] = resource_id
    data = _api("post", f"{DEFAULT_URLS['billing']}/billing/usage-records", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@billing.command("invoice-list")
@click.option("--project")
@click.option("--status")
@click.pass_context
def billing_invoice_list(ctx, project, status):
    params = "?"
    if project: params += f"project_id={project}&"
    if status:  params += f"status={status}&"
    data = _api("get", f"{DEFAULT_URLS['billing']}/billing/invoices{params}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "invoice_number", "project_id", "status", "subtotal", "tax_amount", "total", "created_at"])


@billing.command("invoice-create")
@click.option("--project", "project_id")
@click.option("--period-start")
@click.option("--period-end")
@click.option("--notes")
@click.pass_context
def billing_invoice_create(ctx, project_id, period_start, period_end, notes):
    params = "?"
    if project_id:   params += f"project_id={project_id}&"
    if period_start: params += f"period_start={period_start}&"
    if period_end:   params += f"period_end={period_end}&"
    if notes:        params += f"notes={notes}&"
    data = _api("post", f"{DEFAULT_URLS['billing']}/billing/invoices{params}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@billing.command("invoice-pdf")
@click.argument("invoice_id")
@click.option("--out", default=None, help="Output file path (.pdf or .html)")
@click.pass_context
def billing_invoice_pdf(ctx, invoice_id, out):
    data = _api("post", f"{DEFAULT_URLS['billing']}/billing/invoices/{invoice_id}/pdf", ctx.obj["profile"])
    if "pdf_base64" in data:
        import base64
        out_file = out or f"invoice-{invoice_id}.pdf"
        with open(out_file, "wb") as f:
            f.write(base64.b64decode(data["pdf_base64"]))
        click.echo(f"✓ Invoice PDF saved to {out_file}")
    elif "html" in data:
        out_file = out or f"invoice-{invoice_id}.html"
        with open(out_file, "w") as f:
            f.write(data["html"])
        click.echo(f"✓ Invoice HTML saved to {out_file} (WeasyPrint not installed on server)")
    else:
        _output(data, ctx.obj["fmt"])


@billing.command("summary")
@click.argument("project_id", required=False)
@click.pass_context
def billing_summary(ctx, project_id):
    url = f"{DEFAULT_URLS['billing']}/billing/summary"
    if project_id:
        url += f"/{project_id}"
    data = _api("get", url, ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# EVENT
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("event")
@click.pass_context
def event(ctx):
    """Event log and webhook subscription management"""


@event.command("emit")
@click.option("--type", "event_type", required=True)
@click.option("--resource-type", default="instance")
@click.option("--resource-id")
@click.option("--payload", default="{}")
@click.pass_context
def event_emit(ctx, event_type, resource_type, resource_id, payload):
    try:
        body = json.loads(payload)
    except Exception as exc:
        click.echo(f"❌ Invalid JSON payload: {exc}", err=True)
        sys.exit(1)
    data = {"event_type": event_type, "resource_type": resource_type, "payload": body}
    if resource_id: data["resource_id"] = resource_id
    out = _api("post", f"{DEFAULT_URLS['event']}/events/emit", ctx.obj["profile"], json=data)
    _output(out, ctx.obj["fmt"])


@event.command("type-list")
@click.pass_context
def event_type_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['event']}/events/types", ctx.obj["profile"])
    items = data.get("event_types", data) if isinstance(data, dict) else data
    _output(items, ctx.obj["fmt"], ["event_type", "type", "description"])


@event.command("log-list")
@click.option("--type")
@click.option("--limit", type=int, default=50)
@click.pass_context
def event_log_list(ctx, type, limit):
    params = f"?limit={limit}"
    if type: params += f"&event_type={type}"
    data = _api("get", f"{DEFAULT_URLS['event']}/events/logs{params}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "event_type", "resource_type", "resource_id", "timestamp"])


@event.group("subscription")
@click.pass_context
def event_subscription(ctx):
    """Manage webhook subscriptions"""


@event_subscription.command("create")
@click.argument("name")
@click.option("--endpoint-url", required=True)
@click.option("--event-type", "event_types", multiple=True, required=True)
@click.option("--secret")
@click.pass_context
def event_subscription_create(ctx, name, endpoint_url, event_types, secret):
    payload = {"name": name, "endpoint_url": endpoint_url, "event_types": list(event_types)}
    if secret: payload["secret"] = secret
    data = _api("post", f"{DEFAULT_URLS['event']}/events/subscriptions", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@event_subscription.command("list")
@click.pass_context
def event_subscription_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['event']}/events/subscriptions", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "endpoint_url", "event_types", "is_active"])


@event_subscription.command("show")
@click.argument("sub_id")
@click.pass_context
def event_subscription_show(ctx, sub_id):
    data = _api("get", f"{DEFAULT_URLS['event']}/events/subscriptions/{sub_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@event_subscription.command("delete")
@click.argument("sub_id")
@click.pass_context
def event_subscription_delete(ctx, sub_id):
    _api("delete", f"{DEFAULT_URLS['event']}/events/subscriptions/{sub_id}", ctx.obj["profile"])
    click.echo(f"✓ Subscription {sub_id} deleted")


@event.command("delivery-list")
@click.pass_context
def event_delivery_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['event']}/events/deliveries", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "subscription_id", "event_id", "status", "response_code", "attempts", "created_at"])


@event.command("delivery-retry")
@click.argument("delivery_id")
@click.pass_context
def event_delivery_retry(ctx, delivery_id):
    data = _api("post", f"{DEFAULT_URLS['event']}/events/deliveries/{delivery_id}/retry", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])
