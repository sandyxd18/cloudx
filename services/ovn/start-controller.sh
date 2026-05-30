#!/usr/bin/env bash
set -euo pipefail

OVN_SB_DB="${OVN_SB_DB:-tcp:ovn-sb-db:6642}"
OVN_ENCAP_IP="${OVN_ENCAP_IP:-127.0.0.1}"
PROVIDER_BRIDGE="${PROVIDER_BRIDGE:-br-ex}"
PROVIDER_PHYSNET="${PROVIDER_PHYSNET:-physnet1}"

mkdir -p /var/run/ovn /var/log/ovn

# Wait for host OVS socket (mounted from host via volume)
echo "Waiting for host OVS socket at /var/run/openvswitch/db.sock..."
for i in $(seq 1 30); do
  if [ -S /var/run/openvswitch/db.sock ]; then
    echo "Host OVS socket found."
    break
  fi
  echo "  attempt $i/30, retrying in 2s..."
  sleep 2
done

if [ ! -S /var/run/openvswitch/db.sock ]; then
  echo "ERROR: host OVS socket not available. Ensure openvswitch-switch is running on the host." >&2
  exit 1
fi

# Configure host OVS to connect to OVN SB DB running in container
ovs-vsctl --timeout=10 set open . \
  external_ids:ovn-remote="$OVN_SB_DB" \
  external_ids:ovn-encap-type=geneve \
  external_ids:ovn-encap-ip="$OVN_ENCAP_IP" \
  external_ids:ovn-bridge-mappings="${PROVIDER_PHYSNET}:${PROVIDER_BRIDGE}" || true

echo "Host OVS configured: ovn-remote=$OVN_SB_DB, bridge-mappings=${PROVIDER_PHYSNET}:${PROVIDER_BRIDGE}"

# Run ovn-controller against HOST OVS socket directly
exec ovn-controller \
  unix:/var/run/openvswitch/db.sock \
  --pidfile=/var/run/ovn/ovn-controller.pid \
  --no-chdir \
  --log-file=/dev/stdout
