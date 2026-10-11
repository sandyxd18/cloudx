import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output


@cli.group("alarm")
@click.pass_context
def alarm(ctx):
    """Threshold alarms on telemetry metrics (events go to cloudx.events)"""


@alarm.command("list")
@click.pass_context
def alarm_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/alarms", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "metric_type", "statistic", "comparison", "threshold", "state"])


@alarm.command("show")
@click.argument("alarm_id")
@click.pass_context
def alarm_show(ctx, alarm_id):
    _output(_api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/alarms/{alarm_id}", ctx.obj["profile"]), ctx.obj["fmt"])


@alarm.command("create")
@click.argument("name")
@click.option("--metric", "metric_type", required=True, help="e.g. cpu_percent")
@click.option("--comparison", type=click.Choice([">", ">=", "<", "<="]), required=True)
@click.option("--threshold", type=float, required=True)
@click.option("--period", type=int, default=300, show_default=True, help="window in seconds")
@click.option("--statistic", type=click.Choice(["avg", "min", "max", "sum", "count"]), default="avg")
@click.option("--evaluation-periods", type=int, default=1, show_default=True)
@click.option("--instance", "instance_id", default=None, help="limit to one instance")
@click.pass_context
def alarm_create(ctx, name, metric_type, comparison, threshold, period, statistic, evaluation_periods, instance_id):
    payload = {"name": name, "metric_type": metric_type, "comparison": comparison, "threshold": threshold,
               "period": period, "statistic": statistic, "evaluation_periods": evaluation_periods}
    if instance_id:
        payload["instance_id"] = instance_id
    _output(_api("post", f"{DEFAULT_URLS['telemetry']}/telemetry/alarms", ctx.obj["profile"], json=payload), ctx.obj["fmt"])


@alarm.command("history")
@click.argument("alarm_id")
@click.pass_context
def alarm_history(ctx, alarm_id):
    data = _api("get", f"{DEFAULT_URLS['telemetry']}/telemetry/alarms/{alarm_id}/history", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["at", "old_state", "new_state", "value", "reason"])


@alarm.command("delete")
@click.argument("alarm_id")
@click.pass_context
def alarm_delete(ctx, alarm_id):
    _api("delete", f"{DEFAULT_URLS['telemetry']}/telemetry/alarms/{alarm_id}", ctx.obj["profile"])
    click.echo(f"Alarm {alarm_id} deleted")