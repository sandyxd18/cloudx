#!/usr/bin/env bash
set -euo pipefail

OVN_NB_DB="${OVN_NB_DB:-tcp:127.0.0.1:6641}"
OVN_SB_DB="${OVN_SB_DB:-tcp:127.0.0.1:6642}"

mkdir -p /var/run/ovn

nb_host=$(echo "$OVN_NB_DB" | cut -d: -f2)
nb_port=$(echo "$OVN_NB_DB" | cut -d: -f3)
sb_host=$(echo "$OVN_SB_DB" | cut -d: -f2)
sb_port=$(echo "$OVN_SB_DB" | cut -d: -f3)

until nc -z "$nb_host" "$nb_port" && nc -z "$sb_host" "$sb_port"; do
  echo "waiting for OVN databases ($nb_host:$nb_port, $sb_host:$sb_port)..."
  sleep 2
done

exec ovn-northd \
  --ovnnb-db="$OVN_NB_DB" \
  --ovnsb-db="$OVN_SB_DB" \
  --pidfile=/var/run/ovn/ovn-northd.pid \
  --no-chdir \
  --log-file=/dev/stdout
