#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
IMAGE_PATH=""
PREFIX="test-mini-mnet-$(date +%s)"
NET1="${PREFIX}-net1"; NET2="${PREFIX}-net2"
SUB1="${PREFIX}-sub1"; SUB2="${PREFIX}-sub2"
PORT2="${PREFIX}-port2"
IMAGE_NAME="${PREFIX}-image"; SERVER_NAME="${PREFIX}-server"
cleanup(){ set +e; compute server delete "$SERVER_NAME" >/dev/null 2>&1; networking port delete "$PORT2" >/dev/null 2>&1; networking subnet delete "$SUB1" >/dev/null 2>&1; networking subnet delete "$SUB2" >/dev/null 2>&1; networking network delete "$NET1" >/dev/null 2>&1; networking network delete "$NET2" >/dev/null 2>&1; image delete "$IMAGE_NAME" >/dev/null 2>&1; scripts/cleanup-all.sh --force >/dev/null 2>&1 || true; }
trap cleanup EXIT
while [ "$#" -gt 0 ]; do case "$1" in --image) IMAGE_PATH="${2:-}"; shift 2;; -h|--help) echo "Usage: scripts/test-multi-network.sh --image ./cirros.qcow2"; exit 0;; *) echo "unknown argument: $1" >&2; exit 2;; esac; done
[ -n "$IMAGE_PATH" ] && [ -f "$IMAGE_PATH" ] || { echo "provide --image ./path.qcow2" >&2; exit 2; }
networking network create "$NET1" || true; networking network create "$NET2" || true
networking subnet create "$SUB1" --network "$NET1" --cidr 10.62.0.0/24 --gateway 10.62.0.1 --dhcp || true
networking subnet create "$SUB2" --network "$NET2" --cidr 10.63.0.0/24 --gateway 10.63.0.1 --dhcp || true
networking port create "$PORT2" --network "$NET2"
image upload "$IMAGE_NAME" --file "$IMAGE_PATH" --disk-format qcow2 || true
compute server create "$SERVER_NAME" --image "$IMAGE_NAME" --flavor tiny --network "$NET1"
compute server add-port "$SERVER_NAME" "$PORT2"
compute server show "$SERVER_NAME" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data.get("extra_port_ids")'
compute server remove-port "$SERVER_NAME" "$PORT2"
echo "multi-network smoke test passed"
