#!/usr/bin/env bash
# lab-setup.sh — Idempotent lab resource setup (auth, network, router, image)
# Run AFTER 'make up': ./scripts/lab-setup.sh
#
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

AUTH_API_URL="${CLOUDX_AUTH_URL:-http://localhost:8005}"
NETWORKING_API_URL="${NETWORKING_API_URL:-http://localhost:8001}"
IMAGE_API_URL="${IMAGE_API_URL:-http://localhost:8002}"

PROVIDER_NETWORK_NAME="${PROVIDER_NETWORK_NAME:-public-net}"
PROVIDER_SUBNET_NAME="${PROVIDER_SUBNET_NAME:-public-subnet}"
PROVIDER_CIDR="${PROVIDER_CIDR:-192.168.10.0/24}"
PROVIDER_GATEWAY="${PROVIDER_GATEWAY:-192.168.10.254}"
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

AUTH_ADMIN_USERNAME="${AUTH_ADMIN_USERNAME:-admin}"
AUTH_ADMIN_PASSWORD="${AUTH_ADMIN_PASSWORD:-admin123}"

step() { echo ""; echo "─── $* ───"; }

# ── Wait for APIs ──────────────────────────────────────────────────────────────
step "Waiting for APIs to be ready"
for url in \
    "$AUTH_API_URL/health" \
    "$NETWORKING_API_URL/health" \
    "$IMAGE_API_URL/health" \
    "http://localhost:8003/health" \
    "http://localhost:8004/health" \
    "http://localhost:8006/health"; do
    for i in $(seq 1 30); do
        if curl -fsS "$url" >/dev/null 2>&1; then
            echo "  ✓ $url"
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "  ✗ $url not responding after 60s. Is 'make up' running?" >&2
            exit 1
        fi
        sleep 2
    done
done

# ── Auth: Get admin token ──────────────────────────────────────────────────────
step "Auth: obtaining admin token"
TOKEN_RESPONSE=$(curl -fsS -X POST "$AUTH_API_URL/auth/tokens" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"$AUTH_ADMIN_USERNAME\", \"password\": \"$AUTH_ADMIN_PASSWORD\"}" \
    2>/dev/null || echo "")

if [ -z "$TOKEN_RESPONSE" ]; then
    echo "  ✗ Failed to login to auth-api" >&2
    exit 1
fi

ADMIN_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || true)
if [ -z "$ADMIN_TOKEN" ]; then
    echo "  ✗ Could not parse token from auth-api response" >&2
    exit 1
fi
echo "  ✓ Admin token obtained"

# Save token for cloudx CLI
mkdir -p ~/.cloudx
echo "$ADMIN_TOKEN" > ~/.cloudx/token
chmod 600 ~/.cloudx/token
echo "  ✓ Token saved to ~/.cloudx/token (cloudx CLI ready)"

# Helper: authenticated curl
auth_get()  { curl -fsS "$1" -H "X-Auth-Token: $ADMIN_TOKEN"; }
auth_post() { curl -fsS -X POST "$1" -H "X-Auth-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" -d "$2"; }

resource_exists() { curl -fsS "$1" -H "X-Auth-Token: $ADMIN_TOKEN" >/dev/null 2>&1; }

# ── Provider network (public/external) ────────────────────────────────────────
step "Provider network: $PROVIDER_NETWORK_NAME ($PROVIDER_CIDR)"
if resource_exists "$NETWORKING_API_URL/networks/$PROVIDER_NETWORK_NAME"; then
    echo "  ✓ $PROVIDER_NETWORK_NAME already exists — skipping"
else
    auth_post "$NETWORKING_API_URL/networks" \
        "{\"name\": \"$PROVIDER_NETWORK_NAME\", \"external\": true, \"shared\": false}" >/dev/null
    echo "  ✓ $PROVIDER_NETWORK_NAME created"
fi

# ── Provider subnet ────────────────────────────────────────────────────────────
step "Provider subnet: $PROVIDER_SUBNET_NAME"
if resource_exists "$NETWORKING_API_URL/subnets/$PROVIDER_SUBNET_NAME"; then
    echo "  ✓ $PROVIDER_SUBNET_NAME exists"
else
    SUBNET_PAYLOAD="{
        \"name\": \"$PROVIDER_SUBNET_NAME\",
        \"network\": \"$PROVIDER_NETWORK_NAME\",
        \"cidr\": \"$PROVIDER_CIDR\",
        \"gateway\": \"$PROVIDER_GATEWAY\",
        \"enable_dhcp\": false,
        \"dns_nameservers\": \"$PROVIDER_DNS\",
        \"allocation_start\": \"$PROVIDER_ALLOCATION_START\",
        \"allocation_end\": \"$PROVIDER_ALLOCATION_END\"
    }"
    auth_post "$NETWORKING_API_URL/subnets" "$SUBNET_PAYLOAD" >/dev/null
    echo "  ✓ $PROVIDER_SUBNET_NAME created"
fi

# ── Private network (tenant) ───────────────────────────────────────────────────
step "Private network: $PRIVATE_NETWORK_NAME ($PRIVATE_CIDR)"
if resource_exists "$NETWORKING_API_URL/networks/$PRIVATE_NETWORK_NAME"; then
    echo "  ✓ $PRIVATE_NETWORK_NAME already exists — skipping"
else
    auth_post "$NETWORKING_API_URL/networks" \
        "{\"name\": \"$PRIVATE_NETWORK_NAME\", \"external\": false, \"shared\": false}" >/dev/null
    echo "  ✓ $PRIVATE_NETWORK_NAME created"
fi

if resource_exists "$NETWORKING_API_URL/subnets/$PRIVATE_SUBNET_NAME"; then
    echo "  ✓ $PRIVATE_SUBNET_NAME already exists — skipping"
else
    auth_post "$NETWORKING_API_URL/subnets" "{
        \"name\": \"$PRIVATE_SUBNET_NAME\",
        \"network\": \"$PRIVATE_NETWORK_NAME\",
        \"cidr\": \"$PRIVATE_CIDR\",
        \"gateway\": \"$PRIVATE_GATEWAY\",
        \"enable_dhcp\": true,
        \"dns_nameservers\": \"$PRIVATE_DNS\"
    }" >/dev/null
    echo "  ✓ $PRIVATE_SUBNET_NAME created"
fi

# ── Router ─────────────────────────────────────────────────────────────────────
step "Router: $ROUTER_NAME"
if resource_exists "$NETWORKING_API_URL/routers/$ROUTER_NAME"; then
    echo "  ✓ $ROUTER_NAME already exists — skipping create"
else
    auth_post "$NETWORKING_API_URL/routers" "{\"name\": \"$ROUTER_NAME\"}" >/dev/null
    echo "  ✓ $ROUTER_NAME created"
fi

# add-interface (idempotent — 409 jika sudah terpasang)
auth_post "$NETWORKING_API_URL/routers/$ROUTER_NAME/interfaces" \
    "{\"subnet\": \"$PRIVATE_SUBNET_NAME\"}" >/dev/null 2>&1 \
    && echo "  ✓ Interface $PRIVATE_SUBNET_NAME added to $ROUTER_NAME" \
    || echo "  ✓ Interface $PRIVATE_SUBNET_NAME already attached"

# set-gateway (idempotent)
auth_post "$NETWORKING_API_URL/routers/$ROUTER_NAME/gateway" \
    "{\"network\": \"$PROVIDER_NETWORK_NAME\"}" >/dev/null 2>&1 \
    && echo "  ✓ Gateway → $PROVIDER_NETWORK_NAME" \
    || echo "  ✓ Gateway already configured"

# ── Host route: private subnet via OVN router ──────────────────────────────────
step "Host route: $PRIVATE_CIDR via $PROVIDER_GATEWAY dev ${PROVIDER_BRIDGE:-br-ex}"
PROVIDER_BRIDGE="${PROVIDER_BRIDGE:-br-ex}"
if sudo -n true 2>/dev/null; then
    if ip route show dev "$PROVIDER_BRIDGE" 2>/dev/null | grep -q "^${PRIVATE_CIDR} via "; then
        echo "  ✓ Route already correct — skipping"
    elif ip route show dev "$PROVIDER_BRIDGE" 2>/dev/null | grep -q "^${PRIVATE_CIDR}"; then
        sudo ip route replace "$PRIVATE_CIDR" via "$PROVIDER_GATEWAY" dev "$PROVIDER_BRIDGE"
        echo "  ✓ Route replaced: $PRIVATE_CIDR via $PROVIDER_GATEWAY dev $PROVIDER_BRIDGE"
    else
        sudo ip route add "$PRIVATE_CIDR" via "$PROVIDER_GATEWAY" dev "$PROVIDER_BRIDGE"
        echo "  ✓ Route added: $PRIVATE_CIDR via $PROVIDER_GATEWAY dev $PROVIDER_BRIDGE"
    fi
else
    echo "  ⚠ sudo not available without password — skipping route setup"
    echo "    Run manually: sudo ip route add $PRIVATE_CIDR via $PROVIDER_GATEWAY dev $PROVIDER_BRIDGE"
fi

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
    curl -fsS -X POST "$IMAGE_API_URL/images" \
        -H "X-Auth-Token: $ADMIN_TOKEN" \
        -F "name=$CIRROS_IMAGE_NAME" \
        -F "disk_format=qcow2" \
        -F "visibility=public" \
        -F "file=@$CIRROS_FILE" >/dev/null
    echo "  ✓ $CIRROS_IMAGE_NAME uploaded"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo "  ☁  cloudx lab setup complete!"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  Auth             : $AUTH_API_URL  (admin/$AUTH_ADMIN_PASSWORD)"
echo "  Provider network : $PROVIDER_NETWORK_NAME  ($PROVIDER_CIDR)"
echo "  Private network  : $PRIVATE_NETWORK_NAME  ($PRIVATE_CIDR)"
echo "  Router           : $ROUTER_NAME"
echo "  Image            : $CIRROS_IMAGE_NAME (cirros $CIRROS_VERSION)"
echo ""
echo "  Token saved to ~/.cloudx/token — CLI is ready:"
echo ""
echo "    cloudx server list"
echo "    cloudx keypair create mykey"
echo "    cloudx server create vm1 --image $CIRROS_IMAGE_NAME --flavor tiny \\"
echo "        --network $PRIVATE_NETWORK_NAME --key-name mykey"
echo "    cloudx floating-ip create $PROVIDER_NETWORK_NAME"
echo "    cloudx floating-ip associate <fip> --server vm1 --router $ROUTER_NAME"
echo ""
