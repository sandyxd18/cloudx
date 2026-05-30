#!/usr/bin/env bash
set -euo pipefail

OVN_NB_DB="${OVN_NB_DB:-tcp:ovn-nb-db:6641}"
OVN_SB_DB="${OVN_SB_DB:-tcp:ovn-sb-db:6642}"

mkdir -p /var/run/ovn

until nc -z ovn-nb-db 6641 && nc -z ovn-sb-db 6642; do
  echo "waiting for OVN databases..."
  sleep 2
done

exec ovn-northd \
  --ovnnb-db="$OVN_NB_DB" \
  --ovnsb-db="$OVN_SB_DB" \
  --pidfile=/var/run/ovn/ovn-northd.pid \
  --no-chdir \
  --log-file=/dev/stdout
