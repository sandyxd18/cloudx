import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output


# ═══════════════════════════════════════════════════════════════════════════════
# VOLUME
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def volume(ctx):
    """Manage block storage volumes"""


@volume.command("create")
@click.argument("name")
@click.option("--size", type=int, required=True, help="Size in GB")
@click.option("--type", "volume_type", default=None, help="Volume type name (e.g. gp2, gp3, io2)")
@click.option("--image", help="Create from image (name or ID)")
@click.option("--snapshot", help="Create from snapshot (name or ID)")
@click.option("--source", "source_volume", help="Clone from existing volume")
@click.pass_context
def volume_create(ctx, name, size, volume_type, image, snapshot, source_volume):
    payload: dict = {"name": name, "size": size}
    if volume_type:   payload["volume_type"] = volume_type
    if image:         payload["image_id"] = image
    if snapshot:      payload["snapshot"] = snapshot
    if source_volume: payload["source_volume"] = source_volume
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@volume.command("list")
@click.pass_context
def volume_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['volume']}/volumes", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"],
            ["id", "name", "size", "status", "type", "bootable", "availability_zone"])


@volume.command("show")
@click.argument("ref")
@click.pass_context
def volume_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['volume']}/volumes/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@volume.command("delete")
@click.argument("ref")
@click.pass_context
def volume_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['volume']}/volumes/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Volume {ref} deleted")


@volume.command("extend")
@click.argument("ref")
@click.option("--size", type=int, required=True, help="New size in GB")
@click.pass_context
def volume_extend(ctx, ref, size):
    _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/extend",
         ctx.obj["profile"], json={"new_size": size})
    click.echo(f"✓ Volume {ref} extended to {size} GB")


@volume.command("attach")
@click.argument("ref")
@click.option("--server", required=True)
@click.pass_context
def volume_attach(ctx, ref, server):
    _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/attach",
         ctx.obj["profile"], json={"server": server, "server_id": server})
    click.echo(f"✓ Volume {ref} attached to {server}")


@volume.command("detach")
@click.argument("ref")
@click.pass_context
def volume_detach(ctx, ref):
    _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/detach", ctx.obj["profile"])
    click.echo(f"✓ Volume {ref} detached")


@volume.command("snapshot")
@click.argument("ref")
@click.option("--name", required=True)
@click.pass_context
def volume_snapshot(ctx, ref, name):
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/snapshots",
                ctx.obj["profile"], json={"name": name})
    _output(data, ctx.obj["fmt"])


@volume.command("backup")
@click.argument("ref")
@click.option("--name", required=True)
@click.pass_context
def volume_backup(ctx, ref, name):
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/backup",
                ctx.obj["profile"], json={"name": name})
    _output(data, ctx.obj["fmt"])


@volume.command("transfer-create")
@click.argument("ref")
@click.option("--name", required=True)
@click.pass_context
def volume_transfer_create(ctx, ref, name):
    """Create a transfer token to move volume ownership"""
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes/{ref}/transfer",
                ctx.obj["profile"], json={"name": name})
    click.echo(f"✓ Transfer created. Share this auth_key: {data.get('auth_key', '?')}")
    _output(data, ctx.obj["fmt"])


@volume.command("transfer-accept")
@click.argument("transfer_id")
@click.option("--auth-key", required=True)
@click.pass_context
def volume_transfer_accept(ctx, transfer_id, auth_key):
    data = _api("post", f"{DEFAULT_URLS['volume']}/volume-transfers/{transfer_id}/accept",
                ctx.obj["profile"], json={"auth_key": auth_key})
    click.echo(f"✓ Volume transfer accepted")
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# VOLUME TYPE
# Volume types are AWS EBS-style performance profiles (gp2, gp3, io1, io2, sc1, st1, etc.)
# Admin must create them before any volumes can be created.
# Backend (lvm/ceph) is the storage infrastructure — separate from the type name.
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("volume-type")
@click.pass_context
def volume_type(ctx):
    """Manage volume types (AWS EBS-style performance profiles)"""


@volume_type.command("create")
@click.argument("name", metavar="NAME")
@click.option("--backend", type=click.Choice(["lvm", "ceph"]), required=True,
              help="Storage backend: lvm (block device) or ceph (RBD network disk)")
@click.option("--disk-type", "disk_type",
              type=click.Choice(["sata_ssd", "sas_ssd", "nvme_ssd", "sata_hdd", "sas_hdd"]),
              default="sata_ssd", show_default=True,
              help="Hardware tier hint (informational)")
@click.option("--max-iops", "max_iops", type=int, default=None,
              help="Max IOPS (informational, not enforced). E.g. 3000")
@click.option("--max-throughput", "max_throughput", type=int, default=None,
              help="Max throughput in MB/s (informational). E.g. 125")
@click.option("--description", "-d", default=None, help="Human-readable description")
@click.option("--private", is_flag=True, default=False, help="Make type private (admin-only)")
@click.pass_context
def vt_create(ctx, name, backend, disk_type, max_iops, max_throughput, description, private):
    """Create a new volume type.

    \b
    Examples (AWS EBS equivalents):
      cloudx volume-type create gp2  --backend lvm  --disk-type sata_ssd --max-iops 3000  --max-throughput 125
      cloudx volume-type create gp3  --backend lvm  --disk-type sas_ssd  --max-iops 16000 --max-throughput 1000
      cloudx volume-type create io1  --backend lvm  --disk-type nvme_ssd --max-iops 64000 --max-throughput 4000
      cloudx volume-type create io2  --backend ceph --disk-type nvme_ssd --max-iops 64000 --max-throughput 4000
      cloudx volume-type create sc1  --backend lvm  --disk-type sata_hdd --max-iops 250   --max-throughput 250
      cloudx volume-type create st1  --backend lvm  --disk-type sas_hdd  --max-iops 500   --max-throughput 500
    """
    payload = {
        "name":           name,
        "backend":        backend,
        "disk_type":      disk_type,
        "max_iops":       max_iops,
        "max_throughput": max_throughput,
        "description":    description,
        "is_public":      not private,
    }
    data = _api("post", f"{DEFAULT_URLS['volume']}/volume-types", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@volume_type.command("list")
@click.pass_context
def vt_list(ctx):
    """List all volume types."""
    data = _api("get", f"{DEFAULT_URLS['volume']}/volume-types", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"],
            ["name", "disk_type", "backend", "max_iops", "max_throughput", "description", "is_public"])


@volume_type.command("show")
@click.argument("ref")
@click.pass_context
def vt_show(ctx, ref):
    """Show details of a volume type."""
    data = _api("get", f"{DEFAULT_URLS['volume']}/volume-types/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@volume_type.command("set")
@click.argument("ref")
@click.option("--description", "-d", default=None)
@click.option("--max-iops", "max_iops", type=int, default=None)
@click.option("--max-throughput", "max_throughput", type=int, default=None)
@click.option("--public/--private", default=None)
@click.pass_context
def vt_set(ctx, ref, description, max_iops, max_throughput, public):
    """Update mutable fields of a volume type (admin only)."""
    payload: dict = {}
    if description is not None:    payload["description"] = description
    if max_iops is not None:       payload["max_iops"] = max_iops
    if max_throughput is not None: payload["max_throughput"] = max_throughput
    if public is not None:         payload["is_public"] = public
    if not payload:
        click.echo("Nothing to update. Use --description, --max-iops, --max-throughput, or --public/--private.")
        return
    data = _api("patch", f"{DEFAULT_URLS['volume']}/volume-types/{ref}", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@volume_type.command("delete")
@click.argument("ref")
@click.pass_context
def vt_delete(ctx, ref):
    """Delete a volume type (admin only). Fails if any volumes use this type."""
    _api("delete", f"{DEFAULT_URLS['volume']}/volume-types/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Volume type {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT (volume)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def snapshot(ctx):
    """Manage volume snapshots"""


@snapshot.command("list")
@click.pass_context
def snapshot_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['volume']}/snapshots", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "volume_id", "size", "status"])


@snapshot.command("show")
@click.argument("ref")
@click.pass_context
def snapshot_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['volume']}/snapshots/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@snapshot.command("delete")
@click.argument("ref")
@click.pass_context
def snapshot_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['volume']}/snapshots/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Snapshot {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# VOLUME BACKUP (Volume backup to object storage)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("backup")
@click.pass_context
def backup_group(ctx):
    """Manage full volume backups to object storage"""


@backup_group.command("list")
@click.pass_context
def volume_backup_list(ctx):
    """List all volume backups"""
    data = _api("get", f"{DEFAULT_URLS['volume']}/backups", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "volume_id", "size_gb", "status"])


@backup_group.command("show")
@click.argument("ref")
@click.pass_context
def volume_backup_show(ctx, ref):
    """Show details of a volume backup"""
    data = _api("get", f"{DEFAULT_URLS['volume']}/backups/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@backup_group.command("create")
@click.argument("volume_ref")
@click.option("--name", default=None, help="Backup name")
@click.option("--description", default=None, help="Backup description")
@click.pass_context
def volume_backup_create(ctx, volume_ref, name, description):
    """Create a backup of a volume to object storage"""
    payload = {}
    if name: payload["name"] = name
    if description: payload["description"] = description
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes/{volume_ref}/backup", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@backup_group.command("restore")
@click.argument("backup_id")
@click.option("--name", default=None, help="Name for the restored volume")
@click.pass_context
def volume_backup_restore(ctx, backup_id, name):
    """Restore a backup into a new volume"""
    payload = {}
    if name: payload["name"] = name
    data = _api("post", f"{DEFAULT_URLS['volume']}/backups/{backup_id}/restore", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@backup_group.command("delete")
@click.argument("backup_id")
@click.pass_context
def volume_backup_delete(ctx, backup_id):
    """Delete a volume backup"""
    _api("delete", f"{DEFAULT_URLS['volume']}/backups/{backup_id}", ctx.obj["profile"])
    click.echo(f"✓ Backup {backup_id} deletion requested")
