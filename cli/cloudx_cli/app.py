import click

from cloudx_cli.common import _load_config

# ── Main group ─────────────────────────────────────────────────────────────────
CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("--profile", "-p", default="default", help="Config profile name")
@click.option("--output", "-o", "fmt", default=None,
              type=click.Choice(["json", "table"]), help="Output format")
@click.pass_context
def cli(ctx, profile, fmt):
    """☁  cloudx — Cloud management CLI"""
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile
    # Determine output format
    if fmt is None:
        cfg = _load_config(profile)
        fmt = cfg.get("output", "table")
    ctx.obj["fmt"] = fmt
