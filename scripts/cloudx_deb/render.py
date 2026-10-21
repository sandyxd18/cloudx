"""Text renderers for the files a CloudX package installs: wrapper, systemd unit and nginx snippet."""
from .manifest import Unit

PYTHON = "/opt/cloudx/venv/bin/python"
SERVICES = "/opt/cloudx/services"


def _require(u: Unit, *kinds: str) -> None:
    if u.kind not in kinds:
        raise ValueError(f"{u.name}: kind {u.kind} is not {' or '.join(kinds)}")


def render_wrapper(u: Unit) -> str:
    _require(u, "api", "daemon")
    app_dir = f"{SERVICES}/{u.dir}"
    if u.kind == "api":
        call = (f'run_api(name="{u.name}", app_dir="{app_dir}", port={u.port}, '
                f'workers={u.workers}, frontend="{u.frontend}")')
        func = "run_api"
    else:
        call = f'run_daemon(name="{u.name}", app_dir="{app_dir}")'
        func = "run_daemon"
    return f"#!{PYTHON}\nfrom shared.launcher import {func}\n\n{call}\n"


def _title(name: str) -> str:
    return " ".join("API" if w == "api" else w.capitalize() for w in name.split("-"))


def render_api_unit(u: Unit) -> str:
    _require(u, "api")
    return f"""[Unit]
Description=CloudX {_title(u.name)} Service
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User={u.user}
Group=cloudx
UMask=0007
WorkingDirectory={SERVICES}/{u.dir}
ExecStart=/usr/bin/{u.package}
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""


def render_daemon_unit(u: Unit, source: str) -> str:
    """The source unit with only ExecStart= pointing at the wrapper."""
    _require(u, "daemon")
    out = source.splitlines(keepends=True)
    idx = [i for i, line in enumerate(out) if line.startswith("ExecStart=")]
    if len(idx) != 1:
        raise ValueError(f"{u.name}: expected one ExecStart= line in the source unit, found {len(idx)}")
    ending = out[idx[0]][len(out[idx[0]].rstrip("\r\n")):]
    out[idx[0]] = f"ExecStart=/usr/bin/{u.package}{ending}"
    return "".join(out)


def render_nginx(u: Unit) -> str:
    _require(u, "api")
    if u.frontend != "nginx":
        raise ValueError(f"{u.name}: frontend is {u.frontend}, there is no nginx snippet")
    upstream = f"cloudx_{u.name.replace('-', '_')}"
    streaming = ""
    if u.streaming:
        streaming = ("        client_max_body_size 0;\n"
                     "        proxy_request_buffering off;\n"
                     "        proxy_read_timeout 3600;\n")
    return f"""upstream {upstream} {{
    server unix:/run/cloudx/{u.name}.sock;
}}

server {{
    listen {u.port};

    location / {{
        proxy_pass http://{upstream};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
{streaming}    }}
}}
"""
