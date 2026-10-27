# Security policy

## Supported versions

Only the latest release gets security fixes: the newest 1.x tag.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private reporting instead:
<https://github.com/sandyxd18/cloudx/security/advisories/new>

Include the affected version (`dpkg -l 'cloudx-*'`, or the tag you built from), what you did, and what you expected
to happen. A short proof of concept helps; a working exploit against someone else's cloud does not.

You will get an answer within a week. This is a one-person project, so a fix can take longer than that, but you will be
told where it stands.

## What counts

Worth reporting: authentication or token bypass, one project reaching another project's resources, a service that
accepts internal (`/internal/*`) calls without the internal token, secrets written to logs or world-readable files,
privilege escalation from a service user to root on the host.

Known limitations that are documented, such as the metadata route that needs `setup-metadata-route.sh` on compute nodes
or an `/metrics` endpoint that needs its bearer token, are not vulnerabilities by themselves.

## Hardening notes for operators

- Secrets (`auth_secret_key`, `internal_token`, the admin password, the key manager master key) are generated at install
  time and live in `/etc/cloudx/cloudx.conf`, mode 640, owner `root:cloudx`. Copy the same file to every node; do not
  commit it anywhere.
- Rotate the RabbitMQ and database passwords with the runbook in `docs/deb-install.md` (section 6.5) if they ever show up in a log
  or a ticket.
- Do not expose the internal API ports (the services listen on the management network) to the provider network.
