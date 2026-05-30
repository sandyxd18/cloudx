#!/usr/bin/env bash
# lab-setup.sh — Idempotent lab resource setup (networks, router, image)
# Run AFTER 'make up': ./scripts/lab-setup.sh
# Safe to re-run — skips resources that already exist.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── Load .env ──────────────────────────────────────────────────────────────────
if [ -f "$ROOT_DIR/.env" ]; then
    set -a; source "$ROOT_DIR/.env"; set +a
elif [ -f "$ROOT_DIR/.env.example" ]; then
    echo "⚠ No .env found — using .env.example defaults"
    set -a; source "$ROOT_DIR/.env.example"; set +a
fi

NETWORKING_API_URL="${NETWORKING_API_URL:-http://localhost:8001}"
IMAGE_API_URL="${IMAGE_API_URL:-http://localhost:8002}"

PROVIDER_NETWORK_NAME="${PROVIDER_NETWORK_NAME:-public-net}"
PROVIDER_SUBNET_NAME="${PROVIDER_SUBNET_NAME:-public-subnet}"
PROVIDER_CIDR="${PROVIDER_CIDR:-192.168.10.0/24}"
PROVIDER_GATEWAY="${PROVIDER_GATEWAY:-192.168.10.1}"
PROVIDER_PHYSNET="${PROVIDER_PHYSNET:-physnet1}"
PROVIDER_ALLOCATION_START="${PROVIDER_ALLOCATION_START:-}"
PROVIDER_ALLOCATION_END="${PROVIDER_ALLOCATION_END:-}"
PROVIDER_DNS="${PROVIDER_DNS:-8.8.8.8}"

PRIVATE_NETWORK_NAME="${PRIVATE_NETWORK_NAME:-private-net}"
PRIVATE_SUBNET_NAME="${PRIVATE_SUBNET_NAME:-private-subnet}"
PRIVATE_CIDR="${PRIVATE_CIDR:-192.168.12.0/24}"
PRIVATE_GATEWAY="${PRIVATE_GATEWAY:-192.168.12.1}"
PRIVATE_DNS="${PRIVATE_DNS:-8.8.8.8}"

ROUTER_NAME="${ROUTER_NAME:-router1}"

CIRROS_VERSION="${CIRROS_VERSION:-0.6.2}"
CIRROS_IMAGE_NAME="${CIRROS_IMAGE_NAME:-cirros}"
CIRROS_ARCH="${CIRROS_ARCH:-x86_64}"
CIRROS_URL="https://download.cirros-cloud.net/${CIRROS_VERSION}/cirros-${CIRROS_VERSION}-${CIRROS_ARCH}-disk.img"

step() { echo ""; echo "─── $* ───"; }

# ── Wait for APIs ──────────────────────────────────────────────────────────────
step "Waiting for APIs"
for url in \
    "$NETWORKING_API_URL/health" \
    "$IMAGE_API_URL/health" \
    "http://localhost:8003/health" \
    "http://localhost:8004/health"; do
    for i in $(seq 1 30); do
        if curl -fsS "$url" >/dev/null 2>&1; then
            echo "  ✓ $url"
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "  ✗ $url not responding. Is 'make up' running?" >&2
            exit 1
        fi
        sleep 2
    done
done

# ── Helpers ────────────────────────────────────────────────────────────────────
resource_exists() {
    local url="$1"
    curl -fsS "$url" >/dev/null 2>&1
}

# ── Provider network ───────────────────────────────────────────────────────────
step "Provider network: $PROVIDER_NETWORK_NAME ($PROVIDER_CIDR)"
if resource_exists "$NETWORKING_API_URL/networks/$PROVIDER_NETWORK_NAME"; then
    echo "  ✓ $PROVIDER_NETWORK_NAME already exists — skipping"
else
    networking network create "$PROVIDER_NETWORK_NAME" \
        --external \
        --provider-network-type flat \
        --provider-physical-network "$PROVIDER_PHYSNET"
    echo "  ✓ $PROVIDER_NETWORK_NAME created"
fi

if resource_exists "$NETWORKING_API_URL/subnets/$PROVIDER_SUBNET_NAME"; then
    echo "  ✓ $PROVIDER_SUBNET_NAME already exists — skipping"
else
    SUBNET_ARGS=(
        "$PROVIDER_SUBNET_NAME"
        --network "$PROVIDER_NETWORK_NAME"
        --cidr "$PROVIDER_CIDR"
        --gateway "$PROVIDER_GATEWAY"
        --no-dhcp
        --dns "$PROVIDER_DNS"
    )
    [ -n "$PROVIDER_ALLOCATION_START" ] && SUBNET_ARGS+=(--allocation-start "$PROVIDER_ALLOCATION_START")
    [ -n "$PROVIDER_ALLOCATION_END" ]   && SUBNET_ARGS+=(--allocation-end   "$PROVIDER_ALLOCATION_END")
    networking subnet create "${SUBNET_ARGS[@]}"
    echo "  ✓ $PROVIDER_SUBNET_NAME created"
fi

# ── Private network ────────────────────────────────────────────────────────────
step "Private network: $PRIVATE_NETWORK_NAME ($PRIVATE_CIDR)"
if resource_exists "$NETWORKING_API_URL/networks/$PRIVATE_NETWORK_NAME"; then
    echo "  ✓ $PRIVATE_NETWORK_NAME already exists — skipping"
else
    networking network create "$PRIVATE_NETWORK_NAME"
    echo "  ✓ $PRIVATE_NETWORK_NAME created"
fi

if resource_exists "$NETWORKING_API_URL/subnets/$PRIVATE_SUBNET_NAME"; then
    echo "  ✓ $PRIVATE_SUBNET_NAME already exists — skipping"
else
    networking subnet create "$PRIVATE_SUBNET_NAME" \
        --network "$PRIVATE_NETWORK_NAME" \
        --cidr "$PRIVATE_CIDR" \
        --gateway "$PRIVATE_GATEWAY" \
        --dhcp \
        --dns "$PRIVATE_DNS"
    echo "  ✓ $PRIVATE_SUBNET_NAME created"
fi

# ── Router ─────────────────────────────────────────────────────────────────────
step "Router: $ROUTER_NAME"
if resource_exists "$NETWORKING_API_URL/routers/$ROUTER_NAME"; then
    echo "  ✓ $ROUTER_NAME already exists — skipping create"
else
    networking router create "$ROUTER_NAME"
    echo "  ✓ $ROUTER_NAME created"
fi

# add-interface is idempotent — networking-api returns 409 if already attached
networking router add-interface "$ROUTER_NAME" \
    --subnet "$PRIVATE_SUBNET_NAME" 2>/dev/null \
    && echo "  ✓ Interface $PRIVATE_SUBNET_NAME added" \
    || echo "  ✓ Interface $PRIVATE_SUBNET_NAME already attached"

# set-gateway is idempotent — overwrites if already set
networking router set-gateway "$ROUTER_NAME" "$PROVIDER_NETWORK_NAME" \
    && echo "  ✓ Gateway → $PROVIDER_NETWORK_NAME (gateway_chassis auto-set)" \
    || echo "  ✓ Gateway already configured"

# ── CirrOS image ───────────────────────────────────────────────────────────────
step "CirrOS image: $CIRROS_IMAGE_NAME v${CIRROS_VERSION}"
if resource_exists "$IMAGE_API_URL/images/$CIRROS_IMAGE_NAME"; then
    echo "  ✓ $CIRROS_IMAGE_NAME already in image service — skipping"
else
    CIRROS_FILE="/tmp/cirros-${CIRROS_VERSION}-${CIRROS_ARCH}.img"
    if [ ! -f "$CIRROS_FILE" ]; then
        echo "  ↓ Downloading from $CIRROS_URL"
        if command -v wget >/dev/null 2>&1; then
            wget -q --show-progress -O "$CIRROS_FILE" "$CIRROS_URL"
        else
            curl -L --progress-bar -o "$CIRROS_FILE" "$CIRROS_URL"
        fi
        echo "  ✓ Downloaded to $CIRROS_FILE"
    else
        echo "  ✓ Already downloaded: $CIRROS_FILE"
    fi
    image upload "$CIRROS_IMAGE_NAME" --file "$CIRROS_FILE" --disk-format qcow2
    echo "  ✓ $CIRROS_IMAGE_NAME uploaded"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "  Lab setup complete!"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  Provider network : $PROVIDER_NETWORK_NAME  ($PROVIDER_CIDR)"
echo "  Private network  : $PRIVATE_NETWORK_NAME  ($PRIVATE_CIDR)"
echo "  Router           : $ROUTER_NAME"
echo "  Image            : $CIRROS_IMAGE_NAME (cirros $CIRROS_VERSION)"
echo ""
echo "  Quick start:"
echo "    compute keypair create mykey > mykey.pem && chmod 600 mykey.pem"
echo "    compute server create vm1 --image $CIRROS_IMAGE_NAME --flavor tiny \\"
echo "        --network $PRIVATE_NETWORK_NAME --key-name mykey"
echo "    networking floating-ip create $PROVIDER_NETWORK_NAME"
echo "    networking floating-ip associate <fip> --server vm1 --router $ROUTER_NAME"
echo ""
