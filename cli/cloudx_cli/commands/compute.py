import os
import sys

import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output


# ═══════════════════════════════════════════════════════════════════════════════
# SERVER (Compute)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def server(ctx):
    """Manage compute instances"""


@server.command("create")
@click.argument("name")
@click.option("--image", required=True)
@click.option("--flavor", default="tiny")
@click.option("--network", "networks", multiple=True, required=True,
              help="Network to attach; repeat it for more NICs. The first one is the primary network")
@click.option("--key-name")
@click.option("--security-group", "security_groups", multiple=True)
@click.option("--user-data", type=click.Path())
@click.option("--volume-size", type=int, help="Root volume size GB (boot from volume)")
@click.option("--ipv6", is_flag=True, help="Also give the server an IPv6 address (the network needs an IPv6 subnet)")
@click.pass_context
def server_create(ctx, name, image, flavor, networks, key_name, security_groups, user_data, volume_size, ipv6):
    repeated = sorted({n for n in networks if networks.count(n) > 1})
    if repeated:
        # the API drops a repeated network without a word: the server would get fewer NICs than asked for
        raise click.UsageError(f"network {repeated[0]} is given twice; each --network adds one NIC on a different network")
    payload = {"name": name, "image": image, "flavor": flavor, "network": networks[0]}
    if len(networks) > 1: payload["networks"] = list(networks[1:])
    if key_name:          payload["key_name"] = key_name
    if security_groups:   payload["security_groups"] = list(security_groups)
    if volume_size:       payload["volume_size"] = volume_size
    if ipv6:              payload["ipv6"] = True
    if user_data:
        with open(user_data) as f:
            payload["user_data"] = f.read()
    data = _api("post", f"{DEFAULT_URLS['compute']}/servers", ctx.obj["profile"], json=payload)
    _output(data, ctx.obj["fmt"])


@server.command("list")
@click.pass_context
def server_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"],
            ["id", "name", "status", "flavor", "ip_address", "floating_ip"])


@server.command("show")
@click.argument("ref")
@click.pass_context
def server_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@server.command("delete")
@click.argument("ref")
@click.pass_context
def server_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['compute']}/servers/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} deleted")


@server.command("start")
@click.argument("ref")
@click.pass_context
def server_start(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/start", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} started")


@server.command("stop")
@click.argument("ref")
@click.pass_context
def server_stop(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/stop", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} stopped")


@server.command("reboot")
@click.argument("ref")
@click.option("--hard", is_flag=True)
@click.pass_context
def server_reboot(ctx, ref, hard):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/reboot",
         ctx.obj["profile"], json={"type": "hard" if hard else "soft"})
    click.echo(f"✓ Server {ref} rebooted")


@server.command("pause")
@click.argument("ref")
@click.pass_context
def server_pause(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/pause", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} paused")


@server.command("resume")
@click.argument("ref")
@click.pass_context
def server_resume(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/resume", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} resumed")


@server.command("suspend")
@click.argument("ref")
@click.pass_context
def server_suspend(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/suspend", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} suspended")


@server.command("unsuspend")
@click.argument("ref")
@click.pass_context
def server_unsuspend(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/unsuspend", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} unsuspended")


@server.command("resize")
@click.argument("ref")
@click.option("--flavor", required=True)
@click.pass_context
def server_resize(ctx, ref, flavor):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/resize",
         ctx.obj["profile"], json={"flavor": flavor})
    click.echo(f"✓ Server {ref} resized to {flavor}")


@server.command("rebuild")
@click.argument("ref")
@click.option("--image", required=True)
@click.pass_context
def server_rebuild(ctx, ref, image):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/rebuild",
         ctx.obj["profile"], json={"image": image})
    click.echo(f"✓ Server {ref} rebuilding with image {image}")


@server.command("rescue")
@click.argument("ref")
@click.option("--image", help="Rescue image (defaults to server's original image)")
@click.pass_context
def server_rescue(ctx, ref, image):
    payload = {}
    if image: payload["image"] = image
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/rescue",
         ctx.obj["profile"], json=payload)
    click.echo(f"✓ Server {ref} in rescue mode")


@server.command("unrescue")
@click.argument("ref")
@click.pass_context
def server_unrescue(ctx, ref):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/unrescue", ctx.obj["profile"])
    click.echo(f"✓ Server {ref} unrescued")


@server.command("console")
@click.argument("ref")
@click.pass_context
def server_console(ctx, ref):
    """Open serial console (Ctrl+] to exit)"""
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}/console", ctx.obj["profile"])
    import shutil
    virsh_uri = "qemu:///system"
    domain = data.get("domain_name", ref)
    if shutil.which("virsh") is None:
        click.echo(f"❌ 'virsh' is not installed on this host.", err=True)
        click.echo(f"To access the console, either install libvirt-clients, or run from the compute-worker container:", err=True)
        click.echo(f"  sudo virsh -c qemu:///system console {domain}", err=True)
        sys.exit(1)
    os.execvp("virsh", ["virsh", "-c", virsh_uri, "console", domain])


@server.command("console-url")
@click.argument("ref")
@click.pass_context
def server_console_url(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}/console-url", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@server.command("logs")
@click.argument("ref")
@click.option("--lines", "-n", default=50)
@click.pass_context
def server_logs(ctx, ref, lines):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}/logs?lines={lines}", ctx.obj["profile"])
    click.echo(data.get("output", ""))


@server.command("ssh-info")
@click.argument("ref")
@click.pass_context
def server_ssh_info(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}/ssh-info", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@server.command("metadata")
@click.argument("ref")
@click.pass_context
def server_metadata(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/servers/{ref}/metadata", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@server.command("set-password")
@click.argument("ref")
@click.option("--password", prompt=True, hide_input=True)
@click.pass_context
def server_set_password(ctx, ref, password):
    _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/password",
         ctx.obj["profile"], json={"password": password})
    click.echo(f"✓ Password updated for {ref}")


@server.command("snapshot")
@click.argument("ref")
@click.option("--name", required=True)
@click.pass_context
def server_snapshot(ctx, ref, name):
    data = _api("post", f"{DEFAULT_URLS['compute']}/servers/{ref}/snapshots",
                ctx.obj["profile"], json={"name": name})
    _output(data, ctx.obj["fmt"])


@server.group("port")
def server_port():
    """Manage server network ports"""


@server_port.command("add")
@click.argument("server_ref")
@click.option("--network", help="Create a new port on this network and attach it")
@click.option("--port", "port_ref", help="Attach an existing port")
@click.pass_context
def server_port_add(ctx, server_ref, network, port_ref):
    if bool(network) == bool(port_ref):
        raise click.UsageError("give exactly one of --network or --port")
    if network:
        port_ref = _api("post", f"{DEFAULT_URLS['network']}/ports", ctx.obj["profile"],
                        json={"name": f"{server_ref}-port", "network": network})["id"]
    data = _api("post", f"{DEFAULT_URLS['compute']}/servers/{server_ref}/ports",
                ctx.obj["profile"], json={"port": port_ref})
    _output(data, ctx.obj["fmt"])


@server_port.command("remove")
@click.argument("server_ref")
@click.argument("port_ref")
@click.pass_context
def server_port_remove(ctx, server_ref, port_ref):
    _api("delete", f"{DEFAULT_URLS['compute']}/servers/{server_ref}/ports/{port_ref}",
         ctx.obj["profile"])
    click.echo(f"✓ Port {port_ref} detached")


@server.group("volume")
def server_volume():
    """Manage server volumes"""


@server_volume.command("attach")
@click.argument("server_ref")
@click.option("--volume", required=True)
@click.pass_context
def server_volume_attach(ctx, server_ref, volume):
    # block-store resolves the volume's path and host and then calls compute; the compute endpoint alone needs both
    data = _api("post", f"{DEFAULT_URLS['volume']}/volumes/{volume}/attach",
                ctx.obj["profile"], json={"server": server_ref, "server_id": server_ref})
    _output(data, ctx.obj["fmt"])


@server_volume.command("detach")
@click.argument("server_ref")
@click.option("--volume", required=True)
@click.pass_context
def server_volume_detach(ctx, server_ref, volume):
    _api("post", f"{DEFAULT_URLS['volume']}/volumes/{volume}/detach", ctx.obj["profile"])
    click.echo(f"✓ Volume {volume} detached")


# ═══════════════════════════════════════════════════════════════════════════════
# FLAVOR
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def flavor(ctx):
    """Manage compute flavors"""


@flavor.command("list")
@click.pass_context
def flavor_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/flavors", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"], ["id", "name", "vcpus", "ram_mb", "disk_gb"])


@flavor.command("show")
@click.argument("ref")
@click.pass_context
def flavor_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/flavors/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@flavor.command("create")
@click.argument("name")
@click.option("--vcpus", type=int, required=True)
@click.option("--ram", type=int, required=True, help="RAM in MB")
@click.option("--disk", type=int, required=True, help="Disk in GB")
@click.pass_context
def flavor_create(ctx, name, vcpus, ram, disk):
    data = _api("post", f"{DEFAULT_URLS['compute']}/flavors", ctx.obj["profile"],
                json={"name": name, "vcpus": vcpus, "ram": ram, "disk": disk})
    _output(data, ctx.obj["fmt"])


@flavor.command("delete")
@click.argument("ref")
@click.pass_context
def flavor_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['compute']}/flavors/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Flavor {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# KEYPAIR
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def keypair(ctx):
    """Manage SSH keypairs"""


@keypair.command("create")
@click.argument("name")
@click.pass_context
def keypair_create(ctx, name):
    data = _api("post", f"{DEFAULT_URLS['compute']}/keypairs", ctx.obj["profile"],
                json={"name": name})
    if "private_key" in data:
        fname = f"{name}.pem"
        with open(fname, "w") as f:
            f.write(data["private_key"])
        os.chmod(fname, 0o600)
        click.echo(f"✓ Keypair {name} created. Private key saved to {fname}")
    else:
        _output(data, ctx.obj["fmt"])


@keypair.command("import")
@click.argument("name")
@click.option("--public-key-file", type=click.Path(exists=True), required=True)
@click.pass_context
def keypair_import(ctx, name, public_key_file):
    with open(public_key_file) as f:
        pub_key = f.read().strip()
    data = _api("post", f"{DEFAULT_URLS['compute']}/keypairs/import", ctx.obj["profile"],
                json={"name": name, "public_key": pub_key})
    _output(data, ctx.obj["fmt"])


@keypair.command("list")
@click.pass_context
def keypair_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/keypairs", ctx.obj["profile"])
    _output(data.get("items", data), ctx.obj["fmt"], ["name", "fingerprint", "created_at"])


@keypair.command("show")
@click.argument("name")
@click.pass_context
def keypair_show(ctx, name):
    data = _api("get", f"{DEFAULT_URLS['compute']}/keypairs/{name}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@keypair.command("delete")
@click.argument("name")
@click.pass_context
def keypair_delete(ctx, name):
    _api("delete", f"{DEFAULT_URLS['compute']}/keypairs/{name}", ctx.obj["profile"])
    click.echo(f"✓ Keypair {name} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# SERVER GROUP
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("server-group")
@click.pass_context
def server_group(ctx):
    """Manage server groups (anti-affinity)"""


@server_group.command("create")
@click.argument("name")
@click.option("--policy", type=click.Choice(["anti-affinity", "affinity", "soft-anti-affinity"]), default="anti-affinity")
@click.pass_context
def sg_create(ctx, name, policy):
    data = _api("post", f"{DEFAULT_URLS['compute']}/server-groups", ctx.obj["profile"],
                json={"name": name, "policy": policy})
    _output(data, ctx.obj["fmt"])


@server_group.command("list")
@click.pass_context
def sg_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/server-groups", ctx.obj["profile"])
    _output(data.get("items", []), ctx.obj["fmt"], ["id", "name", "policy", "members"])


@server_group.command("show")
@click.argument("ref")
@click.pass_context
def sg_show(ctx, ref):
    data = _api("get", f"{DEFAULT_URLS['compute']}/server-groups/{ref}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@server_group.command("delete")
@click.argument("ref")
@click.pass_context
def sg_delete(ctx, ref):
    _api("delete", f"{DEFAULT_URLS['compute']}/server-groups/{ref}", ctx.obj["profile"])
    click.echo(f"✓ Server group {ref} deleted")


# ═══════════════════════════════════════════════════════════════════════════════
# HYPERVISOR (Compute nodes & physical host status)
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("hypervisor")
@click.pass_context
def hypervisor(ctx):
    """Manage and inspect hypervisors and compute nodes"""


@hypervisor.command("list")
@click.pass_context
def hypervisor_list(ctx):
    """List all compute hypervisor nodes"""
    data = _api("get", f"{DEFAULT_URLS['placement']}/compute_nodes", ctx.obj["profile"])
    nodes = data.get("compute_nodes", [])
    _output(nodes, ctx.obj["fmt"], ["host", "name", "status", "running_vms", "cpu_percent_used", "memory_mb_used", "memory_mb_total"])


@hypervisor.command("show")
@click.argument("host")
@click.pass_context
def hypervisor_show(ctx, host):
    """Show details of a specific hypervisor compute node"""
    data = _api("get", f"{DEFAULT_URLS['placement']}/compute_nodes", ctx.obj["profile"])
    nodes = data.get("compute_nodes", [])
    target = next((n for n in nodes if n.get("host") == host or n.get("name") == host or n.get("resource_provider_uuid") == host), None)
    if not target:
        click.echo(f"❌ Hypervisor node '{host}' not found", err=True)
        ctx.exit(1)
    _output(target, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# PLACEMENT
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def placement(ctx):
    """View resource placement and usage"""


@placement.command("usage")
@click.pass_context
def placement_usage(ctx):
    data = _api("get", f"{DEFAULT_URLS['placement']}/usages", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@placement.command("provider-list")
@click.pass_context
def provider_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['placement']}/resource_providers", ctx.obj["profile"])
    _output(data.get("resource_providers", []), ctx.obj["fmt"],
            ["uuid", "name", "generation"])


@placement.command("provider-show")
@click.argument("uuid")
@click.pass_context
def provider_show(ctx, uuid):
    data = _api("get", f"{DEFAULT_URLS['placement']}/resource_providers/{uuid}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


# ═══════════════════════════════════════════════════════════════════════════════
# HOST
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group()
@click.pass_context
def host(ctx):
    """Host / hypervisor management"""


@host.command("check")
@click.pass_context
def host_check(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/host/check", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@host.command("show")
@click.pass_context
def host_show(ctx):
    data = _api("get", f"{DEFAULT_URLS['compute']}/host", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])
