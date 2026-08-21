#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# RabbitMQ cluster entrypoint for CloudX HA
# ═══════════════════════════════════════════════════════════════════════════════
# Seed node starts standalone. Secondary nodes join the seed's cluster.
# Controlled by:
#   RABBITMQ_SEED_NODE  — e.g. rabbit@rabbitmq-1 (the node others join)
#   RABBITMQ_NODENAME   — this node's name, e.g. rabbit@rabbitmq-2
#   RABBITMQ_ERLANG_COOKIE — shared secret (identical on all nodes)
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SEED_NODE="${RABBITMQ_SEED_NODE:-rabbit@rabbitmq-1}"
THIS_NODE="${RABBITMQ_NODENAME:-rabbit@$(hostname)}"

# Start the server in the background using the stock entrypoint.
docker-entrypoint.sh rabbitmq-server &
RABBIT_PID=$!

# Wait until this node's management/app is up.
until rabbitmqctl await_startup 2>/dev/null; do
    echo "waiting for local rabbitmq to boot..."
    sleep 3
done

# Apply the HA policy so all classic queues are mirrored to every node.
# (quorum queues are preferred for new queues, but mirroring keeps the existing
#  classic queues used by the RPC layer highly available.)
rabbitmqctl set_policy ha-all "." \
    '{"ha-mode":"all","ha-sync-mode":"automatic"}' \
    --apply-to queues --priority 0 || true

if [ "$THIS_NODE" != "$SEED_NODE" ]; then
    echo "Joining cluster via seed node ${SEED_NODE}..."
    # Wait for the seed to be reachable before joining.
    SEED_HOST="${SEED_NODE#*@}"
    until rabbitmqctl ping -n "$SEED_NODE" 2>/dev/null; do
        echo "  seed ${SEED_NODE} (${SEED_HOST}) not ready yet, retrying in 5s..."
        sleep 5
    done
    rabbitmqctl stop_app
    rabbitmqctl reset
    rabbitmqctl join_cluster "$SEED_NODE"
    rabbitmqctl start_app
    echo "Joined cluster."
else
    echo "This is the seed node (${SEED_NODE}); running standalone/primary."
fi

wait "$RABBIT_PID"
