#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Ceph client secret for libvirt (compute nodes)
# ==============================================================================
# qemu opens RBD disks with the cephx key of CEPH_USER. libvirt keeps that key as a "secret" and the disk XML refers to
# it by UUID. This script defines the secret from the user's keyring, then tells the compute worker its UUID
# (ceph_secret_uuid, ceph_user in /etc/cloudx/compute-worker/compute-worker.conf). Safe to run again.
#
# Needs on this node: /etc/ceph/ceph.conf and the keyring of the user (default client.cloudx), plus libvirt.
# Usage:
#   sudo ./scripts/setup-ceph-libvirt-secret.sh [--user cloudx] [--keyring /etc/ceph/ceph.client.cloudx.keyring]
# ==============================================================================

CEPH_USER="cloudx"
KEYRING=""
CONF="/etc/cloudx/compute-worker/compute-worker.conf"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) CEPH_USER="$2"; shift 2 ;;
        --keyring) KEYRING="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
KEYRING="${KEYRING:-/etc/ceph/ceph.client.${CEPH_USER}.keyring}"
USAGE_NAME="client.${CEPH_USER} secret"

[[ -r "${KEYRING}" ]] || { echo "Cannot read keyring ${KEYRING}" >&2; exit 1; }
KEY="$(sed -n 's/^[[:space:]]*key[[:space:]]*=[[:space:]]*//p' "${KEYRING}" | head -n1 | tr -d '[:space:]')"  # not awk -F=: the key ends in '=' padding
[[ -n "${KEY}" ]] || { echo "No 'key = ...' line in ${KEYRING}" >&2; exit 1; }

# Reuse the UUID of an existing secret for this user, so a second run does not leave a stray duplicate.
UUID="$(virsh secret-list | awk -v name="${USAGE_NAME}" 'index($0, name) {print $1; exit}')"
if [[ -z "${UUID}" ]]; then
    UUID="$(uuidgen)"
    TMP="$(mktemp)"
    cat > "${TMP}" <<EOF
<secret ephemeral='no' private='no'>
  <uuid>${UUID}</uuid>
  <usage type='ceph'><name>${USAGE_NAME}</name></usage>
</secret>
EOF
    virsh secret-define "${TMP}" >/dev/null
fi
# The key goes through a file: as an argument it would show up in the process list.
KEYFILE="$(mktemp)"
trap 'rm -f "${KEYFILE}" "${TMP:-}"' EXIT
printf '%s' "${KEY}" > "${KEYFILE}"
virsh secret-set-value "${UUID}" --file "${KEYFILE}" >/dev/null
echo "libvirt secret ${UUID} holds the key of client.${CEPH_USER}"

mkdir -p "$(dirname "${CONF}")"
[[ -f "${CONF}" ]] || printf '[DEFAULT]\n' > "${CONF}"
for kv in "ceph_user=${CEPH_USER}" "ceph_secret_uuid=${UUID}"; do
    key="${kv%%=*}"
    sed -i "/^${key} *=/d" "${CONF}"
    sed -i "/^\[DEFAULT\]/a ${key} = ${kv#*=}" "${CONF}"
done
chown --reference="$(dirname "${CONF}")" "${CONF}" 2>/dev/null || true
echo "Wrote ceph_user / ceph_secret_uuid to ${CONF}"
echo "Restart the worker: sudo systemctl restart cloudx-compute-worker"
