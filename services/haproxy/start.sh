#!/usr/bin/env bash
set -euo pipefail

# Generate haproxy.cfg from template using environment variables
echo "Generating haproxy.cfg from template..."
envsubst < /etc/haproxy/haproxy.cfg.template > /etc/haproxy/haproxy.cfg

echo "HAProxy configuration:"
cat /etc/haproxy/haproxy.cfg
echo ""

# Validate config
haproxy -c -f /etc/haproxy/haproxy.cfg
echo "✓ HAProxy config validated"

echo "Starting HAProxy..."
exec haproxy -f /etc/haproxy/haproxy.cfg -W -db
