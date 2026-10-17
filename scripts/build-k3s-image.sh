#!/usr/bin/env bash
# Bake K3s (binary + airgap images) into an Ubuntu cloud image and upload it to Glance as k3s-ubuntu.
# Run as root on the compute node (needs qemu-nbd, qemu-img, curl, cloudx CLI).
set -euo pipefail

K3S_VERSION="v1.30.2+k3s1"
BASE_IMAGE=""
IMAGE_NAME="k3s-ubuntu"
DRY_RUN=false

usage() {
    cat <<EOF
Usage: scripts/build-k3s-image.sh --base-image ubuntu.img [--k3s-version v1.30.2+k3s1] [--name k3s-ubuntu] [--dry-run]
Bakes K3s into a copy of the base image, then uploads it to Glance. The base image is not modified.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --base-image) BASE_IMAGE="$2"; shift ;;
        --k3s-version) K3S_VERSION="$2"; shift ;;
        --name) IMAGE_NAME="$2"; shift ;;
        --dry-run) DRY_RUN=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

[ -n "$BASE_IMAGE" ] || { echo "--base-image is required" >&2; usage >&2; exit 2; }
# the version lands in URLs and paths
[[ "$K3S_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+$ ]] || { echo "invalid --k3s-version: $K3S_VERSION" >&2; exit 2; }
[[ "$IMAGE_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid --name: $IMAGE_NAME" >&2; exit 2; }

URL="https://github.com/k3s-io/k3s/releases/download/${K3S_VERSION/+/%2B}"
WORK="$(mktemp -d /var/tmp/k3s-image.XXXXXX)"
OUT="$WORK/$IMAGE_NAME.qcow2"
MNT="$WORK/mnt"

run() { if [ "$DRY_RUN" = true ]; then echo "+ $*"; else "$@"; fi; }

cleanup() {
    [ "$DRY_RUN" = true ] && { rm -rf "$WORK"; return; }
    mountpoint -q "$MNT" 2>/dev/null && umount "$MNT"
    qemu-nbd --disconnect /dev/nbd0 >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

if [ "$DRY_RUN" != true ]; then
    [ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
    [ -f "$BASE_IMAGE" ] || { echo "base image not found: $BASE_IMAGE" >&2; exit 1; }
    for c in qemu-nbd qemu-img curl cloudx; do command -v "$c" >/dev/null || { echo "missing: $c" >&2; exit 1; }; done
fi

run mkdir -p "$MNT"
run curl -fL -o "$WORK/k3s" "$URL/k3s"
run curl -fL -o "$WORK/airgap.tar.zst" "$URL/k3s-airgap-images-amd64.tar.zst"
run cp "$BASE_IMAGE" "$OUT"
run qemu-img resize "$OUT" +2G
run modprobe nbd max_part=8
run qemu-nbd --connect=/dev/nbd0 "$OUT"
run sleep 2
run growpart /dev/nbd0 1 || true
run e2fsck -fp /dev/nbd0p1 || true
run resize2fs /dev/nbd0p1
run mount /dev/nbd0p1 "$MNT"
run install -m 0755 "$WORK/k3s" "$MNT/usr/local/bin/k3s"
run mkdir -p "$MNT/var/lib/rancher/k3s/agent/images"
run cp "$WORK/airgap.tar.zst" "$MNT/var/lib/rancher/k3s/agent/images/"
run umount "$MNT"
run qemu-nbd --disconnect /dev/nbd0
run cloudx image upload "$IMAGE_NAME" --file "$OUT" --disk-format qcow2
echo "done: image $IMAGE_NAME with K3s $K3S_VERSION"
