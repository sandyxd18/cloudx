#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Prepare a node as an LVM volume server (block-store-worker with iSCSI export)
# ==============================================================================
# Turns a spare disk into the node's volume group backend and sets the per-node environment of
# cloudx-block-store-worker (name of the node, iSCSI export). Compute nodes that attach volumes from other nodes
# only need open-iscsi, which this script installs as well.
#
#   sudo cloudx-setup-storage --host cloudx-storage-01 --portal-ip 192.168.10.12 [--device /dev/sdb]
#
# (installed by the cloudx-storage package; from a checkout it is scripts/prepare-lvm-node.sh)
#
# Options:
#   --host NAME        name of this node as the API knows it (BLOCK_STORE_HOST); must match BLOCK_STORE_LVM_HOSTS
#   --portal-ip IP     management address compute nodes use to reach this node's iSCSI portal (ISCSI_PORTAL_IP)
#   --device DEV       disk to use as the physical volume (default /dev/sdb)
#   --vg NAME          volume group name (default vg-cloudx)
#   --wipe             erase an existing filesystem/signature on the device (refused without it)
#   --local-api        this node also runs cloudx-block-store-api (all-in-one): list --host in its block_store_lvm_hosts
#   --dry-run          print what would be done
# ==============================================================================

HOST_NAME="" PORTAL_IP="" DEVICE="/dev/sdb" VG="vg-cloudx" WIPE=0 DRY=0 LOCAL_API=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST_NAME="$2"; shift 2 ;;
        --portal-ip) PORTAL_IP="$2"; shift 2 ;;
        --device) DEVICE="$2"; shift 2 ;;
        --vg) VG="$2"; shift 2 ;;
        --wipe) WIPE=1; shift ;;
        --local-api) LOCAL_API=1; shift ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) sed -n '4,21p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ -n "$HOST_NAME" && -n "$PORTAL_IP" ]] || { echo "--host and --portal-ip are required (see --help)" >&2; exit 2; }
[[ "$VG" =~ ^[A-Za-z0-9_.+-]+$ ]] || { echo "invalid volume group name: $VG" >&2; exit 2; }
[[ "$HOST_NAME" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "invalid host name: $HOST_NAME" >&2; exit 2; }

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
# --- local api ---
if (( LOCAL_API )); then
    api_conf="${CLOUDX_CONF_DIR:-/etc/cloudx}/block-store-api/block-store-api.conf"
    echo "===> Listing ${HOST_NAME} in block_store_lvm_hosts (${api_conf})"
    if (( DRY )); then
        echo "+ add ${HOST_NAME} to block_store_lvm_hosts in ${api_conf}"
    else
        [[ -f "$api_conf" ]] || { echo "no block-store-api config at ${api_conf}: --local-api is for a node that also runs cloudx-block-store-api" >&2; exit 1; }
        if grep -qE '^block_store_lvm_hosts *=' "$api_conf"; then
            current="$(sed -nE 's/^block_store_lvm_hosts *= *//p' "$api_conf" | head -1)"
            case ",${current// /}," in
                *",${HOST_NAME},"*) ;;
                *) sed -i -E "s|^(block_store_lvm_hosts *= *.*[^ ])|\\1,${HOST_NAME}|" "$api_conf" ;;
            esac
        elif grep -qE '^# *block_store_lvm_hosts *=' "$api_conf"; then
            sed -i -E "0,/^# *block_store_lvm_hosts *=.*/s||block_store_lvm_hosts = ${HOST_NAME}|" "$api_conf"
        else
            echo "block_store_lvm_hosts = ${HOST_NAME}" >> "$api_conf"
        fi
    fi
fi
# --- end local api ---
echo
echo "Done. The volume group ${VG} is created by the worker on its first start (it needs ${DEVICE} empty)."
if (( LOCAL_API )); then
    echo "Next: sudo systemctl restart cloudx-block-store-worker cloudx-block-store-api, then create a volume type"
    echo "      (cloudx volume-type create gp2 --backend lvm --disk-type sata_ssd --max-iops 3000 --max-throughput 125)"
else
    echo "Next: set block_store_lvm_hosts in the block-store-api config on the controller to the names of all storage nodes, then"
    echo "      sudo systemctl restart cloudx-block-store-worker   (see docs/two-node-lvm-lab.md)"
fi
