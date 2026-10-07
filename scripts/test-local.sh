#!/usr/bin/env bash
set -euo pipefail

# Old per-service CLIs (networking/compute/block-store) are now one `cloudx`; scripts parse JSON.
cloudx() { command cloudx -o json "$@"; }
INTERNAL_TOKEN="${INTERNAL_TOKEN:-$(sudo grep -s '^INTERNAL_TOKEN=' /etc/cloudx/cloudx.env 2>/dev/null | cut -d= -f2 || true)}"

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
  cloudx volume delete "$volume" >/dev/null 2>&1
  cloudx keypair delete "$key" >/dev/null 2>&1
  cloudx image delete "$image_name" >/dev/null 2>&1
  cloudx router remove-interface "$router" "$subnet" >/dev/null 2>&1
  cloudx router delete "$router" >/dev/null 2>&1
  cloudx subnet delete "$subnet" >/dev/null 2>&1
  cloudx network delete "$net" >/dev/null 2>&1
  scripts/cleanup-all.sh --dry-run >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Checking health endpoints..."
curl -fsS "$NETWORKING_API_URL/health"
curl -fsS "$IMAGE_API_URL/health"
curl -fsS "$COMPUTE_API_URL/health"
curl -fsS "$BLOCK_STORE_API_URL/health"

echo "Creating network resources..."
cloudx network create "$net"
cloudx subnet create "$subnet" --network "$net" --cidr "10.42.0.0/24" --gateway "10.42.0.1"
cloudx router create "$router"
cloudx router add-interface "$router" --subnet "$subnet"

echo "Uploading image..."
cloudx image upload "$image_name" --file "$dummy_image" --disk-format qcow2

echo "Creating keypair and server..."
cloudx keypair create "$key"
chmod 600 "$key.pem"
cloudx flavor list

echo "Creating volume metadata..."
cloudx volume create "$volume" --size 1

echo "Resource lists:"
cloudx network list
cloudx subnet list
cloudx router list
cloudx image list
cloudx keypair list
cloudx flavor list
cloudx volume list

echo "Local test flow complete."
echo "Run 'make cleanup-all' to inspect/remove local host leftovers."
echo "Run 'docker compose down -v' if you want a full clean reset."
