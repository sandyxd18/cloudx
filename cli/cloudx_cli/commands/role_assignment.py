import click

from cloudx_cli.app import cli
from cloudx_cli.common import DEFAULT_URLS, _api, _output


# ═══════════════════════════════════════════════════════════════════════════════
# ROLE ASSIGNMENTS
# ═══════════════════════════════════════════════════════════════════════════════
@cli.group("role-assignment")
@click.pass_context
def role_assignment(ctx):
    """Manage role assignments"""


@role_assignment.command("list")
@click.option("--project", default=None, help="Filter by project")
@click.option("--user", default=None, help="Filter by user")
@click.pass_context
def role_assignment_list(ctx, project, user):
    params = {}
    if project:
        params["project"] = project
    if user:
        params["user"] = user
    data = _api("get", f"{DEFAULT_URLS['auth']}/role-assignments", ctx.obj["profile"], params=params)
    _output(data.get("items", data), ctx.obj["fmt"], ["user_id", "project_id", "role"])


@role_assignment.command("create")
@click.option("--project", required=True, help="Project name or ID")
@click.option("--user", required=True, help="User name or ID")
@click.option("--role", required=True, help="Role name")
@click.pass_context
def role_assignment_create(ctx, project, user, role):
    _api("put", f"{DEFAULT_URLS['auth']}/projects/{project}/users/{user}/roles/{role}", ctx.obj["profile"])
    click.echo(f"Granted role '{role}' to user '{user}' on project '{project}'")


@role_assignment.command("delete")
@click.option("--project", required=True, help="Project name or ID")
@click.option("--user", required=True, help="User name or ID")
@click.option("--role", required=True, help="Role name")
@click.pass_context
def role_assignment_delete(ctx, project, user, role):
    _api("delete", f"{DEFAULT_URLS['auth']}/projects/{project}/users/{user}/roles/{role}", ctx.obj["profile"])
    click.echo(f"Revoked role '{role}' from user '{user}' on project '{project}'")


# Aliases
role_assignment.add_command(role_assignment_create, name="grant")
role_assignment.add_command(role_assignment_delete, name="revoke")
