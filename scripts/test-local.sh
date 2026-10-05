#!/usr/bin/env bash
set -euo pipefail

NETWORKING_API_URL="${NETWORKING_API_URL:-http://localhost:8001}"
IMAGE_API_URL="${IMAGE_API_URL:-http://localhost:8002}"
COMPUTE_API_URL="${COMPUTE_API_URL:-http://localhost:8003}"
BLOCK_STORE_API_URL="${BLOCK_STORE_API_URL:-http://localhost:8004}"

suffix="$(date +%s)"
net="mini-os-net-$suffix"
subnet="mini-os-subnet-$suffix"
router="mini-os-router-$suffix"
image_name="mini-os-image-$suffix"
server="mini-os-server-$suffix"
key="mini-os-key-$suffix"
volume="mini-os-volume-$suffix"
dummy_image="$(mktemp)"

printf 'cloudx-dummy-image-%s' "$suffix" > "$dummy_image"

cleanup() {
  set +e
  rm -f "$dummy_image" "$key.pem"
  block-store volume delete "$volume" >/dev/null 2>&1
  compute keypair delete "$key" >/dev/null 2>&1
  image delete "$image_name" >/dev/null 2>&1
  networking router remove-interface "$router" --subnet "$subnet" >/dev/null 2>&1
  networking router delete "$router" >/dev/null 2>&1
  networking subnet delete "$subnet" >/dev/null 2>&1
  networking network delete "$net" >/dev/null 2>&1
  scripts/cleanup-all.sh --dry-run >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Checking health endpoints..."
curl -fsS "$NETWORKING_API_URL/health"
curl -fsS "$IMAGE_API_URL/health"
curl -fsS "$COMPUTE_API_URL/health"
curl -fsS "$BLOCK_STORE_API_URL/health"

echo "Creating network resources..."
networking network create "$net"
networking subnet create "$subnet" --network "$net" --cidr "10.42.0.0/24" --gateway "10.42.0.1"
networking router create "$router"
networking router add-interface "$router" --subnet "$subnet"

echo "Uploading image..."
image upload "$image_name" --file "$dummy_image" --disk-format qcow2

echo "Creating keypair and server..."
compute keypair create "$key" > "$key.pem"
chmod 600 "$key.pem"
compute flavor list

echo "Creating volume metadata..."
block-store volume create "$volume" --size 1

echo "Resource lists:"
networking network list
networking subnet list
networking router list
image list
compute keypair list
compute flavor list
block-store volume list

echo "Local test flow complete."
echo "Run 'make cleanup-all' to inspect/remove local host leftovers."
echo "Run 'docker compose down -v' if you want a full clean reset."
