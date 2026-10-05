import json
import os
import sys

import click
import requests

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _headers, _output


# ═══════════════════════════════════════════════════════════════════════════════
# OBJECT STORE
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("object-store")
@click.pass_context
def object_store(ctx):
    """Manage object storage buckets and objects"""


@object_store.command("list")
@click.pass_context
def bucket_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['object-store']}/buckets", ctx.obj["profile"])
    _output(data.get("buckets", data), ctx.obj["fmt"],
            ["id", "name", "visibility", "endpoint_url", "object_count", "size_bytes", "created_at"])


@object_store.command("show")
@click.argument("name")
@click.pass_context
def bucket_show(ctx, name):
    data = _api("get", f"{DEFAULT_URLS['object-store']}/buckets/{name}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@object_store.command("create")
@click.argument("name")
@click.option("--visibility", type=click.Choice(["private", "public-read"]), default="private", help="Bucket visibility")
@click.option("--type-id", help="Bucket type ID (optional)")
@click.pass_context
def bucket_create(ctx, name, visibility, type_id):
    payload = {"name": name, "visibility": visibility}
    if type_id: payload["bucket_type_id"] = type_id
    data = _api("post", f"{DEFAULT_URLS['object-store']}/buckets", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@object_store.command("set-visibility")
@click.argument("name")
@click.argument("visibility", type=click.Choice(["private", "public-read"]))
@click.pass_context
def bucket_set_visibility(ctx, name, visibility):
    data = _api("patch", f"{DEFAULT_URLS['object-store']}/buckets/{name}", ctx.obj["profile"], json={"visibility": visibility})
    _output(data, ctx.obj["fmt"])


@object_store.command("delete")
@click.argument("name")
@click.pass_context
def bucket_delete(ctx, name):
    _api("delete", f"{DEFAULT_URLS['object-store']}/buckets/{name}", ctx.obj["profile"])
    click.echo(f"✓ Bucket {name} deleted")


# ── Object Store Objects ────────────────═══════════════════════════════════════
@object_store.group("object")
@click.pass_context
def object_cmd(ctx):
    """Manage objects inside buckets"""


@object_cmd.command("list")
@click.argument("bucket")
@click.option("--prefix", default="", help="Prefix filter")
@click.pass_context
def object_list(ctx, bucket, prefix):
    url = f"{DEFAULT_URLS['object-store']}/buckets/{bucket}/objects"
    if prefix: url += f"?prefix={prefix}"
    data = _api("get", url, ctx.obj["profile"])
    _output(data.get("objects", data), ctx.obj["fmt"], ["id", "key", "size", "content_type", "created_at"])


@object_cmd.command("put")
@click.argument("bucket")
@click.argument("local_file", type=click.Path(exists=True))
@click.option("--key", default=None, help="Object key in bucket (defaults to local file name)")
@click.pass_context
def object_put(ctx, bucket, local_file, key):
    key = key or os.path.basename(local_file)
    headers = _headers(ctx.obj["profile"])
    headers.pop("Content-Type", None)  # let requests set multipart boundary
    with open(local_file, "rb") as f:
        files = {"file": (key, f)}
        r = requests.put(f"{DEFAULT_URLS['object-store']}/buckets/{bucket}/objects/{key}", headers=headers, files=files, timeout=60)
        if not r.ok:
            click.echo(f"❌ Upload failed (HTTP {r.status_code}): {r.text}", err=True)
            sys.exit(1)
        _output(r.json(), ctx.obj["fmt"])


@object_cmd.command("get")
@click.argument("bucket")
@click.argument("key")
@click.option("--out", default=None, help="Output file path (defaults to printing to stdout or saving by key)")
@click.pass_context
def object_get(ctx, bucket, key, out):
    headers = _headers(ctx.obj["profile"])
    r = requests.get(f"{DEFAULT_URLS['object-store']}/buckets/{bucket}/objects/{key}", headers=headers, timeout=60)
    if not r.ok:
        click.echo(f"❌ Download failed (HTTP {r.status_code}): {r.text}", err=True)
        sys.exit(1)
    if out:
        with open(out, "wb") as f:
            f.write(r.content)
        click.echo(f"✓ Object {key} saved to {out}")
    else:
        sys.stdout.buffer.write(r.content)


@object_cmd.command("delete")
@click.argument("bucket")
@click.argument("key")
@click.pass_context
def object_delete(ctx, bucket, key):
    _api("delete", f"{DEFAULT_URLS['object-store']}/buckets/{bucket}/objects/{key}", ctx.obj["profile"])
    click.echo(f"✓ Object {key} deleted from bucket {bucket}")


# ── Object Store Bucket Types (Admin) ═════════════════════════════════════════
@object_store.group("type")
@click.pass_context
def bucket_type_cmd(ctx):
    """Manage bucket types (admin)"""


@bucket_type_cmd.command("list")
@click.pass_context
def type_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['object-store']}/admin/bucket-types", ctx.obj["profile"])
    _output(data.get("bucket_types", data), ctx.obj["fmt"], ["id", "name", "driver", "description"])


@bucket_type_cmd.command("create")
@click.argument("name")
@click.option("--driver", type=click.Choice(["filesystem", "s3"]), default="filesystem", help="Storage driver")
@click.option("--config", default="{}", help="Config JSON string")
@click.option("--description", default=None, help="Description")
@click.pass_context
def type_create(ctx, name, driver, config, description):
    try:
        cfg_dict = json.loads(config)
    except Exception as exc:
        click.echo(f"❌ Invalid JSON config: {exc}", err=True)
        sys.exit(1)
    payload = {"name": name, "driver": driver, "config": cfg_dict, "description": description}
    data = _api("post", f"{DEFAULT_URLS['object-store']}/admin/bucket-types", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@bucket_type_cmd.command("delete")
@click.argument("ref")
@click.pass_context
def type_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['object-store']}/admin/bucket-types/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Bucket type {ref} deleted")
