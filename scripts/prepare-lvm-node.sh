#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Prepare a node as an LVM volume server (block-store-worker with iSCSI export)
# ==============================================================================
# Turns a spare disk into the node's volume group backend and sets the per-node environment of
# cloudx-block-store-worker (name of the node, iSCSI export). Compute nodes that attach volumes from other nodes
# only need open-iscsi, which this script installs as well.
#
#   sudo ./scripts/prepare-lvm-node.sh --host cloudx-storage-01 --portal-ip 192.168.10.12 [--device /dev/sdb]
#
# Options:
#   --host NAME        name of this node as the API knows it (BLOCK_STORE_HOST); must match BLOCK_STORE_LVM_HOSTS
#   --portal-ip IP     management address compute nodes use to reach this node's iSCSI portal (ISCSI_PORTAL_IP)
#   --device DEV       disk to use as the physical volume (default /dev/sdb)
#   --vg NAME          volume group name (default vg-cloudx)
#   --wipe             erase an existing filesystem/signature on the device (refused without it)
#   --dry-run          print what would be done
# ==============================================================================

HOST_NAME="" PORTAL_IP="" DEVICE="/dev/sdb" VG="vg-cloudx" WIPE=0 DRY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST_NAME="$2"; shift 2 ;;
        --portal-ip) PORTAL_IP="$2"; shift 2 ;;
        --device) DEVICE="$2"; shift 2 ;;
        --vg) VG="$2"; shift 2 ;;
        --wipe) WIPE=1; shift ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) sed -n '4,21p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ -n "$HOST_NAME" && -n "$PORTAL_IP" ]] || { echo "--host and --portal-ip are required (see --help)" >&2; exit 2; }
[[ "$VG" =~ ^[A-Za-z0-9_.+-]+$ ]] || { echo "invalid volume group name: $VG" >&2; exit 2; }

act() { if (( DRY )); then echo "+ $*"; else "$@"; fi; }

if (( ! DRY )) && [[ $EUID -ne 0 ]]; then
    echo "this script must be run as root / with sudo" >&2
    exit 1
fi

echo "===> Checking ${DEVICE}"
if (( ! DRY )); then
    [[ -b "$DEVICE" ]] || { echo "${DEVICE} is not a block device" >&2; exit 1; }
    if lsblk -no MOUNTPOINT "$DEVICE" | grep -q .; then
        echo "${DEVICE} (or one of its partitions) is mounted; unmount it and move its data first" >&2
        exit 1
    fi
    signature="$(lsblk -no FSTYPE "$DEVICE" | sort -u | tr '\n' ' ')"
    if [[ -n "${signature// /}" && "${signature// /}" != "LVM2_member" ]]; then
        if (( WIPE )); then
            wipefs -a "$DEVICE"
        else
            echo "${DEVICE} carries a ${signature}signature; rerun with --wipe to erase it" >&2
            exit 1
        fi
    fi
fi

echo "===> Installing packages (lvm2, targetcli-fb, open-iscsi)"
act apt-get update -qq
act apt-get install -y -qq lvm2 targetcli-fb open-iscsi
act systemctl enable --now iscsid
act systemctl enable --now rtslib-fb-targetctl 2>/dev/null || act systemctl enable --now target

echo "===> Per-node settings for cloudx-block-store-worker"
conf=/etc/cloudx/block-store-worker/block-store-worker.conf
act mkdir -p "$(dirname "$conf")"
if (( DRY )); then
    echo "+ write ${conf}"
else
    cat > "$conf" <<EOF
[DEFAULT]
block_store_host = ${HOST_NAME}
iscsi_export = true
iscsi_portal_ip = ${PORTAL_IP}
storage_lvm_driver = true
storage_nfs_driver = false
lvm_pv_name = ${DEVICE}
lvm_vg_name = ${VG}
EOF
fi
echo
echo "Done. The volume group ${VG} is created by the worker on its first start (it needs ${DEVICE} empty)."
echo "Next: set BLOCK_STORE_LVM_HOSTS on the controller to the names of all storage nodes, then"
echo "      sudo systemctl restart cloudx-block-store-worker   (see docs/two-node-lvm-lab.md)"
