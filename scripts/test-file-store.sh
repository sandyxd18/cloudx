#!/usr/bin/env bash
# File Store lifecycle: share network -> share -> access rule -> extend -> delete. Needs a running lab and `cloudx` on PATH.
# It does not mount the share (that needs a VM with an NFS client; see docs/file-store.md "Terbukti di lab").
set -euo pipefail
cloudx() { command cloudx -o json "$@"; }
jget() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

PREFIX="test-mini-fs-$(date +%s)"
NET="${PREFIX}-net"; SUBNET="${PREFIX}-subnet"; SN="${PREFIX}-sn"; SHARE="${PREFIX}-share"

cleanup() {
  set +e
  [ -n "${SHARE_ID:-}" ] && cloudx file-store share delete "$SHARE_ID" >/dev/null 2>&1
  wait_gone shares "$SHARE_ID"
  [ -n "${SN_ID:-}" ] && cloudx file-store network delete "$SN_ID" >/dev/null 2>&1
  wait_gone networks "$SN_ID"
  cloudx subnet delete "$SUBNET" >/dev/null 2>&1
  cloudx network delete "$NET" >/dev/null 2>&1
}
wait_gone() {  # $1 = share|network list, $2 = id
  [ -n "${2:-}" ] || return 0
  for _ in $(seq 1 40); do
    cloudx file-store "${1%s}" list 2>/dev/null | grep -q "\"$2\"" || return 0
    sleep 3
  done
}
wait_for() {  # $1 = kind (share|network), $2 = id, $3 = wanted status
  for _ in $(seq 1 60); do
    if [ "$1" = network ]; then  # no `network show` in the CLI
      st="$(cloudx file-store network list 2>/dev/null | python3 -c "import json,sys; print(next((n['status'] for n in json.load(sys.stdin) if n['id']=='$2'), ''))" 2>/dev/null || true)"
    else
      st="$(cloudx file-store share show "$2" 2>/dev/null | jget '["status"]' 2>/dev/null || true)"
    fi
    [ "$st" = "$3" ] && return 0
    [ "$st" = "error" ] && { echo "$1 $2 went to error" >&2; return 1; }
    sleep 3
  done
  echo "timed out waiting for $1 $2 to be $3 (last: $st)" >&2; return 1
}
trap cleanup EXIT

cloudx network create "$NET" >/dev/null
cloudx subnet create "$SUBNET" --network "$NET" --cidr 10.62.0.0/24 --gateway 10.62.0.1 --dhcp >/dev/null
NET_ID="$(cloudx network show "$NET" | jget '["id"]')"

SN_ID="$(cloudx file-store network create "$SN" --network "$NET_ID" | jget '["id"]')"
wait_for network "$SN_ID" active

SHARE_ID="$(cloudx file-store share create "$SHARE" --size 1 --network "$SN_ID" | jget '["id"]')"
wait_for share "$SHARE_ID" available
cloudx file-store share show "$SHARE_ID" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["export_location"] and d["size_gb"] == 1, d'

# no rule means nobody may mount; 0.0.0.0/0 must be refused
if cloudx file-store access add "$SHARE_ID" 0.0.0.0/0 >/dev/null 2>&1; then echo "0.0.0.0/0 was accepted" >&2; exit 1; fi
RULE="$(cloudx file-store access add "$SHARE_ID" 10.62.0.5 --level rw | jget '["id"]')"
cloudx file-store access list "$SHARE_ID" | grep -q 10.62.0.5
cloudx file-store access remove "$SHARE_ID" "$RULE"
[ "$(cloudx file-store access list "$SHARE_ID" | jget '.__len__()')" = 0 ]

cloudx file-store share extend "$SHARE_ID" 2 >/dev/null
wait_for share "$SHARE_ID" available
[ "$(cloudx file-store share show "$SHARE_ID" | jget '["size_gb"]')" = 2 ]
echo "file-store lifecycle test passed"
