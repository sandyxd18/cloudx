#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
FORCE=false
VM_PREFIX="${VM_NAME_PREFIX:-mini-os}"
TEST_VM_PREFIX="test-mini-"
TAP_PREFIX="tap-mini-"
TEST_TAP_PREFIX="tap-test-"
INSTANCE_DIR="${COMPUTE_INSTANCE_DIR:-/var/lib/mini-openstack/compute/instances}"
LIBVIRT_URI="${LIBVIRT_URI:-qemu:///system}"

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --force) FORCE=true; DRY_RUN=false ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/cleanup-compute.sh [--dry-run] [--force]

Safely removes libvirt domains, tap interfaces, OVS ports, and instance
directories created by mini OpenStack lab. Prompts before deletion unless
--dry-run or --force is provided.
USAGE
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

actions=()
descriptions=()

add_action() {
  actions+=("$1")
  descriptions+=("$2")
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

if has_cmd virsh; then
  while IFS= read -r domain; do
    [ -z "$domain" ] && continue
    add_action "virsh -c '$LIBVIRT_URI' destroy '$domain' >/dev/null 2>&1 || true; virsh -c '$LIBVIRT_URI' undefine '$domain' --nvram --managed-save >/dev/null 2>&1 || virsh -c '$LIBVIRT_URI' undefine '$domain' >/dev/null 2>&1 || true" "libvirt domain $domain"
  done < <(virsh -c "$LIBVIRT_URI" list --all --name 2>/dev/null | grep -E "^(${VM_PREFIX}-|${TEST_VM_PREFIX})" || true)
else
  echo "skipped libvirt cleanup: virsh not found"
fi

if has_cmd ovs-vsctl; then
  while IFS= read -r port; do
    [ -z "$port" ] && continue
    add_action "ovs-vsctl --if-exists del-port '$port'" "OVS port $port"
  done < <(ovs-vsctl list-ports br-int 2>/dev/null | grep -E "^(${TAP_PREFIX}|${TEST_TAP_PREFIX})" || true)
else
  echo "skipped OVS port cleanup: ovs-vsctl not found"
fi

if has_cmd ip; then
  while IFS= read -r iface; do
    [ -z "$iface" ] && continue
    add_action "ip link delete '$iface'" "tap interface $iface"
  done < <(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1 | grep -E "^(${TAP_PREFIX}|${TEST_TAP_PREFIX})" || true)
else
  echo "skipped tap cleanup: ip command not found"
fi

if [ -d "$INSTANCE_DIR" ]; then
  while IFS= read -r dir; do
    [ -z "$dir" ] && continue
    add_action "rm -rf '$dir'" "instance directory $dir"
  done < <(find "$INSTANCE_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null || true)
fi

if [ "${#actions[@]}" -eq 0 ]; then
  echo "No mini OpenStack lab compute resources found."
  exit 0
fi

echo "Resources selected for cleanup:"
for description in "${descriptions[@]}"; do
  echo " - $description"
done

if [ "$DRY_RUN" = true ]; then
  echo "Dry-run mode: no changes made. Re-run with --force to delete."
  exit 0
fi

if [ "$FORCE" != true ]; then
  printf "Type 'yes' to delete these resources: "
  read -r answer
  if [ "$answer" != "yes" ]; then
    echo "Cleanup cancelled."
    exit 0
  fi
fi

for i in "${!actions[@]}"; do
  echo "deleting: ${descriptions[$i]}"
  eval "${actions[$i]}" || echo "failed or skipped: ${descriptions[$i]}"
done

echo "Compute cleanup complete."
