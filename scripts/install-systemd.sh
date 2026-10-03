#!/usr/bin/env bash
set -euo pipefail

# Installer script to set up CloudX services natively on systemd with /etc/cloudx configuration files.

CLOUDX_USER="cloudx"
CLOUDX_GROUP="cloudx"
ETC_DIR="/etc/cloudx"
OPT_DIR="/opt/cloudx"
SYSTEMD_DIR="/etc/systemd/system"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "===> Setting up /etc/hosts service aliases..."
grep -q "networking-api" /etc/hosts || echo "127.0.0.1 auth-api compute-api networking-api image-api block-store-api placement-api key-manager-api k8s-engine-api telemetry-api billing-api event-api orchestration-api load-balancer-api object-store-api metadata-api rabbitmq postgres" >> /etc/hosts

echo "===> Setting up CloudX system user & group..."
if ! getent group "${CLOUDX_GROUP}" >/dev/null; then
    groupadd -r "${CLOUDX_GROUP}"
fi
if ! id -u "${CLOUDX_USER}" >/dev/null 2>&1; then
    useradd -r -g "${CLOUDX_GROUP}" -d "${OPT_DIR}" -s /sbin/nologin -c "CloudX Service User" "${CLOUDX_USER}"
fi

echo "===> Creating /etc/cloudx configuration directory structure..."
mkdir -p "${ETC_DIR}"

SERVICES=(
    "auth-api"
    "compute-api"
    "conductor"
    "networking-api"
    "image-api"
    "block-store-api"
    "placement-api"
    "key-manager-api"
    "k8s-engine-api"
    "telemetry-api"
    "billing-api"
    "event-api"
    "orchestration-api"
    "load-balancer-api"
    "object-store-api"
)

for svc in "${SERVICES[@]}"; do
    mkdir -p "${ETC_DIR}/${svc}"
    if [[ -f "${SCRIPT_DIR}/etc/cloudx/${svc}/${svc}.conf.sample" ]]; then
        if [[ ! -f "${ETC_DIR}/${svc}/${svc}.conf" ]]; then
            echo "Copying default config sample for ${svc}..."
            cp "${SCRIPT_DIR}/etc/cloudx/${svc}/${svc}.conf.sample" "${ETC_DIR}/${svc}/${svc}.conf"
        fi
    fi
done

chown -R "${CLOUDX_USER}:${CLOUDX_GROUP}" "${ETC_DIR}"
chmod 750 "${ETC_DIR}"

echo "===> Initializing persistent LVM storage volume group (vg-cloudx)..."
if [[ -f "${SCRIPT_DIR}/scripts/setup-storage.sh" ]]; then
    bash "${SCRIPT_DIR}/scripts/setup-storage.sh" || true
fi

echo "===> Creating runtime data directories under /var/lib/mini-openstack..."
mkdir -p /var/lib/mini-openstack/images /var/lib/mini-openstack/object-store /var/lib/mini-openstack/compute
chown -R "${CLOUDX_USER}:${CLOUDX_GROUP}" /var/lib/mini-openstack

echo "===> Syncing codebase to ${OPT_DIR}..."
mkdir -p "${OPT_DIR}"
cp -r "${SCRIPT_DIR}/services" "${OPT_DIR}/"
if [[ -d "${SCRIPT_DIR}/services/dashboard/frontend/dist" ]]; then
    mkdir -p "${OPT_DIR}/services/dashboard/app/static"
    cp -r "${SCRIPT_DIR}/services/dashboard/frontend/dist/"* "${OPT_DIR}/services/dashboard/app/static/"
fi
if [[ -d "${SCRIPT_DIR}/shared" ]]; then
    cp -r "${SCRIPT_DIR}/shared" "${OPT_DIR}/"
fi
cp -r "${SCRIPT_DIR}/cli" "${OPT_DIR}/"
chown -R "${CLOUDX_USER}:${CLOUDX_GROUP}" "${OPT_DIR}"

echo "===> Setting up Python Virtual Environment in ${OPT_DIR}/venv..."
if [[ ! -d "${OPT_DIR}/venv" ]]; then
    python3 -m venv "${OPT_DIR}/venv"
fi
"${OPT_DIR}/venv/bin/pip" install --upgrade pip setuptools wheel
"${OPT_DIR}/venv/bin/pip" install gunicorn uvicorn psycopg2-binary "psycopg[binary]" requests pydantic sqlalchemy cryptography fernet fastapi "python-jose[cryptography]" bcrypt passlib prometheus-client httpx paramiko pika boto3 botocore pyyaml jinja2 alembic psutil python-multipart websockets pymemcache

echo "===> Installing Systemd Unit Files to ${SYSTEMD_DIR}..."
for unit in "${SCRIPT_DIR}/etc/systemd/"*.service; do
    if [[ -f "${unit}" ]]; then
        echo "Installing $(basename "${unit}")..."
        cp "${unit}" "${SYSTEMD_DIR}/"
    fi
done

systemctl daemon-reload
echo "✓ Systemd services installed successfully!"
echo "To start all CloudX services, run:"
echo "  systemctl enable --now cloudx-*"
