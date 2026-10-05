import sys

import click
import requests

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _get_token, _output


# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def image(ctx):
    """Manage images"""


@image.command("list")
@click.option("--visibility", type=click.Choice(["public", "private", "shared", "community"]))
@click.option("--status")
@click.option("--limit", type=int, default=50)
@click.pass_context
def image_list(ctx, visibility, status, limit):
    params = f"?limit={limit}"
    if visibility: params += f"&visibility={visibility}"
    if status:     params += f"&status={status}"
    data = _api("get", f"{DEFAULT_URLS['image']}/images{params}", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"],
            ["id", "name", "status", "visibility", "disk_format", "size"])


@image.command("show")
@click.argument("ref")
@click.pass_context
def image_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['image']}/images/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@image.command("upload")
@click.argument("name")
@click.option("--file", "filepath", type=click.Path(exists=True), required=True)
@click.option("--disk-format", default="qcow2", type=click.Choice(["qcow2", "raw", "vmdk"]))
@click.option("--visibility", default="public", type=click.Choice(["public", "private", "shared"]))
@click.pass_context
def image_upload(ctx, name, filepath, disk_format, visibility):
    token = _get_token(ctx.obj["profile"])
    if not token:
        click.echo("❌ Not authenticated", err=True); sys.exit(1)
    with open(filepath, "rb") as f:
        r = requests.post(
            f"{DEFAULT_URLS['image']}/images",
            headers={"X-Auth-Token": token},
            data={"name": name, "disk_format": disk_format, "visibility": visibility},
            files={"file": f},
            timeout=300,
        )
    if not r.ok:
        click.echo(f"❌ Upload failed: {r.text}", err=True); sys.exit(1)
    _output(r.json(), ctx.obj["fmt"])


@image.command("delete")
@click.argument("ref")
@click.pass_context
def image_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['image']}/images/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Image {ref} deleted")


@image.command("deactivate")
@click.argument("ref")
@click.pass_context
def image_deactivate(ctx, ref):
    _api("post", f"{DEFAULT_URLS['image']}/images/{ref}/deactivate", ctx.obj["profile"])
    click.echo(f"✓ Image {ref} deactivated")


@image.command("reactivate")
@click.argument("ref")
@click.pass_context
def image_reactivate(ctx, ref):
    _api("post", f"{DEFAULT_URLS['image']}/images/{ref}/reactivate", ctx.obj["profile"])
    click.echo(f"✓ Image {ref} reactivated")


@image.command("set-visibility")
@click.argument("ref")
@click.argument("visibility", type=click.Choice(["public", "private", "shared", "community"]))
@click.pass_context
def image_set_visibility(ctx, ref, visibility):
    _api("patch", f"{DEFAULT_URLS['image']}/images/{ref}",
         ctx.obj["profile"], json={"visibility": visibility})
    click.echo(f"✓ Image {ref} visibility set to {visibility}")


@image.group("member")
def image_member():
    """Manage image sharing"""


@image_member.command("add")
@click.argument("image_ref")
@click.argument("project_id")
@click.pass_context
def image_member_add(ctx, image_ref, project_id):
    _api("post", f"{DEFAULT_URLS['image']}/images/{image_ref}/members",
         ctx.obj["profile"], json={"project_id": project_id})
    click.echo(f"✓ Image shared with project {project_id}")


@image_member.command("list")
@click.argument("image_ref")
@click.pass_context
def image_member_list(ctx, image_ref):
    data = _api("get", f"{DEFAULT_URLS['image']}/images/{image_ref}/members", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["project_id", "status"])


@image_member.command("remove")
@click.argument("image_ref")
@click.argument("project_id")
@click.pass_context
def image_member_remove(ctx, image_ref, project_id):
    _api("delete", f"{DEFAULT_URLS['image']}/images/{image_ref}/members/{project_id}",
         ctx.obj["profile"])
    click.echo(f"✓ Sharing removed for project {project_id}")
