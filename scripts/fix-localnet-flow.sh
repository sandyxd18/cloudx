#!/usr/bin/env bash
# fix-localnet-flow.sh
# Inject table=0 flow untuk localnet port ke br-int secara manual.
# Diperlukan ketika ovn-controller tidak otomatis membuat flow (OVN 24.x issue).
# Safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -f "$ROOT_DIR/.env" ]; then set -a; source "$ROOT_DIR/.env"; set +a; fi

OVN_NB_DB="${OVN_NB_DB:-tcp:localhost:6641}"
OVN_SB_DB="${OVN_SB_DB:-tcp:localhost:6642}"
PROVIDER_NETWORK_NAME="${PROVIDER_NETWORK_NAME:-public-net}"
PROVIDER_PHYSNET="${PROVIDER_PHYSNET:-physnet1}"
PATCH_PORT_INT="patch-br-int-to-br-ex"

echo "═══ Localnet Flow Fix ═══"

# ── Verify patch ports exist ───────────────────────────────────────────────────
if ! sudo ovs-vsctl get interface "$PATCH_PORT_INT" ofport >/dev/null 2>&1; then
    echo "Creating patch ports first..."
    sudo ovs-vsctl \
        -- --may-exist add-port br-int patch-br-int-to-br-ex \
        -- set interface patch-br-int-to-br-ex type=patch \
               options:peer=patch-br-ex-to-br-int \
               "external_ids:ovn-localnet-port=${PROVIDER_PHYSNET}" \
        -- --may-exist add-port "${PROVIDER_BRIDGE:-br-ex}" patch-br-ex-to-br-int \
        -- set interface patch-br-ex-to-br-int type=patch \
               options:peer=patch-br-int-to-br-ex \
               "external_ids:ovn-bridge=${PROVIDER_BRIDGE:-br-ex}"
fi

PATCH_OFPORT=$(sudo ovs-vsctl get interface "$PATCH_PORT_INT" ofport)
echo "  patch port ofport: $PATCH_OFPORT"

# ── Get public-net datapath info from OVN SB ──────────────────────────────────
LSP_LOCALNET="${PROVIDER_NETWORK_NAME}-localnet"
PB_JSON=$(sudo ovn-sbctl --db="$OVN_SB_DB" --format=json find Port_Binding \
    "logical_port=${LSP_LOCALNET}" 2>/dev/null)

DATAPATH_UUID=$(echo "$PB_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
rows = d.get('data', [])
if rows:
    cols = d['headings']
    row = dict(zip(cols, rows[0]))
    dp = row.get('datapath', '')
    print(dp[1] if isinstance(dp, list) else dp)
" 2>/dev/null)

PORT_TK=$(echo "$PB_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
rows = d.get('data', [])
if rows:
    cols = d['headings']
    row = dict(zip(cols, rows[0]))
    print(row.get('tunnel_key', 0))
" 2>/dev/null)

if [ -z "$DATAPATH_UUID" ]; then
    echo "ERROR: Cannot find Port_Binding for $LSP_LOCALNET. Is networking-api running?" >&2
    exit 1
fi

echo "  datapath UUID: $DATAPATH_UUID"
echo "  port tunnel_key: $PORT_TK"

# Get datapath tunnel_key (= metadata value in OVS flows)
DP_TK=$(sudo ovn-sbctl --db="$OVN_SB_DB" get Datapath_Binding "$DATAPATH_UUID" tunnel_key 2>/dev/null)
echo "  datapath tunnel_key (metadata): $DP_TK"

# CT zones (derived from tunnel_key pattern: REG13=DP_TK+1, REG12=DP_TK, REG11=DP_TK+3)
# Based on observed pattern: private-net (TK=2) → REG13=3, REG12=2, REG11=5
REG13=$((DP_TK + 1))
REG12=$DP_TK
REG11=$((DP_TK + 3))

echo "  CT zones: REG13=$REG13 REG12=$REG12 REG11=$REG11"

# ── Inject table=0 flow ────────────────────────────────────────────────────────
# Cookie 0x1ab uses a non-OVN cookie so OVN won't delete it via its flow mgmt
COOKIE="0x1abe1"
FLOW="table=0,priority=100,in_port=${PATCH_OFPORT}"
ACTIONS="load:0x${REG13}->NXM_NX_REG13[0..15],"
ACTIONS+="load:0x${REG11}->NXM_NX_REG11[],"
ACTIONS+="load:0x${REG12}->NXM_NX_REG12[],"
ACTIONS+="load:0x${DP_TK}->OXM_OF_METADATA[],"
ACTIONS+="load:0x${PORT_TK}->NXM_NX_REG14[],"
ACTIONS+="load:0->NXM_NX_REG13[16..31],"
ACTIONS+="resubmit(,8)"

echo "  Injecting: $FLOW"
sudo ovs-ofctl add-flow br-int "cookie=${COOKIE},-1,${FLOW},actions=${ACTIONS}"
echo "  ✓ table=0 flow injected"

# ── Verify ─────────────────────────────────────────────────────────────────────
echo ""
echo "Current table=0 flows:"
sudo ovs-ofctl dump-flows br-int table=0

echo ""
echo "Test ARP:"
sudo arping -I "${PROVIDER_BRIDGE:-br-ex}" -c 2 "${PROVIDER_ALLOCATION_START:-192.168.10.110}" 2>&1 || true
