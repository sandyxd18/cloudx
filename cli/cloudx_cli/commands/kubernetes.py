import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output

# ── Managed Kubernetes Engine Commands ─────────────────────────────────────────

@cli.group("k8s-template")
@click.pass_context
def k8s_template(ctx):
    """Manage Kubernetes Cluster Templates"""


@k8s_template.command("list")
@click.pass_context
def k8s_template_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['k8s-engine']}/cluster-templates", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "k8s_version", "master_flavor", "worker_flavor", "image_id"])


@k8s_template.command("create")
@click.argument("name")
@click.option("--image", "image_id", required=True, help="Image ID for k8s nodes")
@click.option("--k8s-version", default="v1.30.2+k3s1")
@click.option("--master-flavor", default="m1.medium")
@click.option("--worker-flavor", default="m1.small")
@click.option("--network-driver", default="ovn")
@click.option("--volume-driver", default="cloudx-csi")
@click.pass_context
def k8s_template_create(ctx, name, image_id, k8s_version, master_flavor, worker_flavor, network_driver, volume_driver):
    body = {
        "name": name,
        "image_id": image_id,
        "k8s_version": k8s_version,
        "master_flavor": master_flavor,
        "worker_flavor": worker_flavor,
        "network_driver": network_driver,
        "volume_driver": volume_driver
    }
    data = _api("post", f"{DEFAULT_URLS['k8s-engine']}/cluster-templates", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])


@k8s_template.command("show")
@click.argument("template_id")
@click.pass_context
def k8s_template_show(ctx, template_id):
    data = _api("get", f"{DEFAULT_URLS['k8s-engine']}/cluster-templates/{template_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@k8s_template.command("delete")
@click.argument("template_id")
@click.pass_context
def k8s_template_delete(ctx, template_id):
    _api("delete", f"{DEFAULT_URLS['k8s-engine']}/cluster-templates/{template_id}", ctx.obj["profile"])
    click.echo(f"✓ Cluster Template {template_id} deleted")


@cli.group("cluster")
@click.pass_context
def k8s_cluster(ctx):
    """Manage Kubernetes Clusters"""


@k8s_cluster.command("list")
@click.pass_context
def k8s_cluster_list(ctx):
    data = _api("get", f"{DEFAULT_URLS['k8s-engine']}/clusters", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"], ["id", "name", "template_id", "master_count", "worker_count", "status", "master_ip", "status_reason"])


@k8s_cluster.command("create")
@click.argument("name")
@click.option("--template", "template_id", required=True, help="Cluster Template ID")
@click.option("--network", "network_id", required=True, help="Network ID")
@click.option("--masters", "master_count", default=1, type=int)
@click.option("--workers", "worker_count", default=2, type=int)
@click.pass_context
def k8s_cluster_create(ctx, name, template_id, network_id, master_count, worker_count):
    body = {
        "name": name,
        "template_id": template_id,
        "network_id": network_id,
        "master_count": master_count,
        "worker_count": worker_count
    }
    data = _api("post", f"{DEFAULT_URLS['k8s-engine']}/clusters", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])


@k8s_cluster.command("show")
@click.argument("cluster_id")
@click.pass_context
def k8s_cluster_show(ctx, cluster_id):
    data = _api("get", f"{DEFAULT_URLS['k8s-engine']}/clusters/{cluster_id}", ctx.obj["profile"])
    _output(data, ctx.obj["fmt"])


@k8s_cluster.command("scale")
@click.argument("cluster_id")
@click.option("--workers", "worker_count", required=True, type=int)
@click.pass_context
def k8s_cluster_scale(ctx, cluster_id, worker_count):
    body = {"worker_count": worker_count}
    data = _api("post", f"{DEFAULT_URLS['k8s-engine']}/clusters/{cluster_id}/scale", ctx.obj["profile"], json=body)
    _output(data, ctx.obj["fmt"])


@k8s_cluster.command("kubeconfig")
@click.argument("cluster_id")
@click.pass_context
def k8s_cluster_kubeconfig(ctx, cluster_id):
    data = _api("get", f"{DEFAULT_URLS['k8s-engine']}/clusters/{cluster_id}/kubeconfig", ctx.obj["profile"])
    click.echo(data.get("kubeconfig", ""))


@k8s_cluster.command("delete")
@click.argument("cluster_id")
@click.pass_context
def k8s_cluster_delete(ctx, cluster_id):
    _api("delete", f"{DEFAULT_URLS['k8s-engine']}/clusters/{cluster_id}", ctx.obj["profile"])
    click.echo(f"✓ Kubernetes Cluster {cluster_id} deleted")
