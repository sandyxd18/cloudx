import re
import subprocess

import click

from cloudx_cli.app import cli

_UNIT_NAME = re.compile(r"[A-Za-z0-9_.@:-]+")


def _unit(service_name: str) -> str:
    """cloudx-<name>; the name ends up as an argument of systemctl/journalctl, so only unit-name characters are allowed."""
    if not _UNIT_NAME.fullmatch(service_name):
        raise click.BadParameter(f"invalid service name: {service_name!r}")
    return service_name if service_name.startswith("cloudx-") else f"cloudx-{service_name}"


def _exec(cmd: list[str]) -> int:
    """Run a command attached to the terminal (no shell). 127 if the program does not exist on this host."""
    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        return 127


# ── Systemd Service Management Commands ────────────────────────────────────────

@cli.group("service")
@click.pass_context
def cloudx_service(ctx):
    """Manage CloudX systemd services & daemon lifecycle"""


@cloudx_service.command("list")
@click.pass_context
def cloudx_service_list(ctx):
    """List all installed CloudX systemd services"""
    res = _exec(["systemctl", "list-units", "cloudx-*", "--no-pager"])
    if res != 0:
        click.echo("Systemd services list unavailable or not running on Linux systemd host.")


@cloudx_service.command("status")
@click.argument("service_name", required=False)
@click.pass_context
def cloudx_service_status(ctx, service_name):
    """Check status of a specific service or all CloudX services"""
    target = _unit(service_name) if service_name else "cloudx-*"
    _exec(["systemctl", "status", target, "--no-pager"])


@cloudx_service.command("restart")
@click.argument("service_name")
@click.pass_context
def cloudx_service_restart(ctx, service_name):
    """Restart a CloudX systemd service"""
    target = _unit(service_name)
    res = _exec(["sudo", "systemctl", "restart", target])
    if res == 0:
        click.echo(f"✓ Service {target} restarted")
    else:
        click.echo(f"❌ Failed to restart {target}")


@cloudx_service.command("logs")
@click.argument("service_name")
@click.option("--follow", "-f", is_flag=True, help="Follow journal output")
@click.option("--lines", "-n", default=50, help="Number of journal lines to show")
@click.pass_context
def cloudx_service_logs(ctx, service_name, follow, lines):
    """View journalctl logs for a CloudX service"""
    target = _unit(service_name)
    flags = ["-f"] if follow else ["-n", str(lines)]
    _exec(["journalctl", "-u", target, *flags, "--no-pager"])
