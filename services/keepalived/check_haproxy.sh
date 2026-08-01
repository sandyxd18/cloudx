#!/usr/bin/env bash
# check_haproxy.sh — Health check script called by Keepalived
# Returns 0 (healthy) if HAProxy stats endpoint responds, 1 (unhealthy) otherwise.
set -euo pipefail

HAPROXY_STATS_PORT="${HAPROXY_STATS_PORT:-8404}"

if curl -sf --max-time 2 "http://127.0.0.1:${HAPROXY_STATS_PORT}/stats" > /dev/null 2>&1; then
    exit 0
fi
exit 1
