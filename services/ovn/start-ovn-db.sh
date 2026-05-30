#!/usr/bin/env bash
set -euo pipefail

kind="${1:-}"
mkdir -p /var/lib/ovn /var/run/ovn /var/run/openvswitch

if [ "$kind" = "nb" ]; then
  db=/var/lib/ovn/ovnnb_db.db
  schema=/usr/share/ovn/ovn-nb.ovsschema
  port=6641
elif [ "$kind" = "sb" ]; then
  db=/var/lib/ovn/ovnsb_db.db
  schema=/usr/share/ovn/ovn-sb.ovsschema
  port=6642
else
  echo "usage: start-ovn-db.sh nb|sb" >&2
  exit 2
fi

if [ ! -f "$db" ]; then
  ovsdb-tool create "$db" "$schema"
fi

exec ovsdb-server "$db" \
  --remote=ptcp:"$port":0.0.0.0 \
  --remote=punix:/var/run/ovn/"$kind".sock \
  --unixctl=/var/run/ovn/"$kind".ctl \
  --pidfile=/var/run/ovn/"$kind".pid \
  --no-chdir \
  --log-file=/dev/stdout
