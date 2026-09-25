#!/usr/bin/env bash
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
export HAPROXY_STATS_PORT="${HAPROXY_STATS_PORT:-8404}"
export AUTH_PORT="${AUTH_PORT:-8005}"
export NETWORKING_PORT="${NETWORKING_PORT:-8001}"
export IMAGE_PORT="${IMAGE_PORT:-8002}"
export COMPUTE_PORT="${COMPUTE_PORT:-8003}"
export BLOCK_STORE_PORT="${BLOCK_STORE_PORT:-8004}"
export PLACEMENT_PORT="${PLACEMENT_PORT:-8006}"
export OBJECT_STORE_PORT="${OBJECT_STORE_PORT:-8007}"
export LOAD_BALANCER_PORT="${LOAD_BALANCER_PORT:-8008}"
export DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"

# Per-service backend host lists (comma-separated). Single controller → 127.0.0.1.
# Multi-node HA → e.g. AUTH_BACKENDS=10.0.0.11,10.0.0.12,10.0.0.13
AUTH_BACKENDS="${AUTH_BACKENDS:-127.0.0.1}"
NETWORKING_BACKENDS="${NETWORKING_BACKENDS:-127.0.0.1}"
IMAGE_BACKENDS="${IMAGE_BACKENDS:-127.0.0.1}"
COMPUTE_BACKENDS="${COMPUTE_BACKENDS:-127.0.0.1}"
BLOCK_STORE_BACKENDS="${BLOCK_STORE_BACKENDS:-127.0.0.1}"
PLACEMENT_BACKENDS="${PLACEMENT_BACKENDS:-127.0.0.1}"
OBJECT_STORE_BACKENDS="${OBJECT_STORE_BACKENDS:-127.0.0.1}"
LOAD_BALANCER_BACKENDS="${LOAD_BALANCER_BACKENDS:-127.0.0.1}"
DASHBOARD_BACKENDS="${DASHBOARD_BACKENDS:-127.0.0.1}"

# ── Helper: render "server nodeN <host>:<port> check ..." lines ────────────────
render_servers() {
    local hosts="$1" port="$2"
    local idx=1 line=""
    local IFS=','
    for host in $hosts; do
        host="$(echo "$host" | xargs)"   # trim whitespace
        [ -z "$host" ] && continue
        line="${line}    server node${idx} ${host}:${port} check inter 5s fall 3 rise 2\n"
        idx=$((idx + 1))
    done
    printf '%b' "$line"
}

echo "Generating haproxy.cfg from template..."
# First substitute the ${PORT} / ${STATS} style vars, keep %%SERVERS%% markers.
envsubst < /etc/haproxy/haproxy.cfg.template > /etc/haproxy/haproxy.cfg.tmp

# Then replace the %%..._SERVERS%% markers with rendered server lines.
replace_marker() {
    local marker="$1" servers="$2"
    # Use awk to substitute the marker line with multi-line server block.
    awk -v marker="$marker" -v repl="$servers" '
        $0 ~ marker { printf "%s", repl; next }
        { print }
    ' /etc/haproxy/haproxy.cfg.tmp > /etc/haproxy/haproxy.cfg.tmp2
    mv /etc/haproxy/haproxy.cfg.tmp2 /etc/haproxy/haproxy.cfg.tmp
}

replace_marker "%%AUTH_SERVERS%%"        "$(render_servers "$AUTH_BACKENDS" "$AUTH_PORT")"
replace_marker "%%NETWORKING_SERVERS%%"  "$(render_servers "$NETWORKING_BACKENDS" "$NETWORKING_PORT")"
replace_marker "%%IMAGE_SERVERS%%"       "$(render_servers "$IMAGE_BACKENDS" "$IMAGE_PORT")"
replace_marker "%%COMPUTE_SERVERS%%"     "$(render_servers "$COMPUTE_BACKENDS" "$COMPUTE_PORT")"
replace_marker "%%BLOCK_STORE_SERVERS%%" "$(render_servers "$BLOCK_STORE_BACKENDS" "$BLOCK_STORE_PORT")"
replace_marker "%%PLACEMENT_SERVERS%%"   "$(render_servers "$PLACEMENT_BACKENDS" "$PLACEMENT_PORT")"
replace_marker "%%OBJECT_STORE_SERVERS%%" "$(render_servers "$OBJECT_STORE_BACKENDS" "$OBJECT_STORE_PORT")"
replace_marker "%%LOAD_BALANCER_SERVERS%%" "$(render_servers "$LOAD_BALANCER_BACKENDS" "$LOAD_BALANCER_PORT")"
replace_marker "%%DASHBOARD_SERVERS%%"   "$(render_servers "$DASHBOARD_BACKENDS" "$DASHBOARD_PORT")"

mv /etc/haproxy/haproxy.cfg.tmp /etc/haproxy/haproxy.cfg

echo "HAProxy configuration:"
cat /etc/haproxy/haproxy.cfg
echo ""

# Validate config
haproxy -c -f /etc/haproxy/haproxy.cfg
echo "✓ HAProxy config validated"

echo "Starting HAProxy..."
exec haproxy -f /etc/haproxy/haproxy.cfg -W -db
