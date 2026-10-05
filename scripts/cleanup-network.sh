#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=true
FORCE=false
REMOVE_BRIDGES=false
PROJECT_PREFIX="mini-os-"
TEST_PREFIX="test-mini-"
TAP_PREFIX="tap-mini-"
TEST_TAP_PREFIX="tap-test-"
PROJECT_MARKER="cloudx"

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --force) FORCE=true; DRY_RUN=false ;;
    --remove-bridges|--delete-project-bridges) REMOVE_BRIDGES=true ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/cleanup-network.sh [--dry-run] [--force] [--remove-bridges]

Safely removes local networking leftovers that belong to mini OpenStack lab.
Default mode is dry-run. It targets mini-os-/test-mini- resources and OVS
bridges br-int/br-ex only with --remove-bridges when they are project-marked.
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

ovs_marked() {
  local bridge="$1"
  [ "$(ovs-vsctl --if-exists get bridge "$bridge" external_ids:cloudx 2>/dev/null | tr -d '"')" = "true" ]
}

if has_cmd ovs-vsctl; then
  while IFS= read -r port; do
    [ -z "$port" ] && continue
    add_action "ovs-vsctl --if-exists del-port '$port'" "OVS port $port"
  done < <(ovs-vsctl list-ports br-int 2>/dev/null | grep -E "^(${TAP_PREFIX}|${TEST_TAP_PREFIX})" || true)

  while IFS= read -r bridge; do
    [ -z "$bridge" ] && continue
    case "$bridge" in
      ${PROJECT_PREFIX}*|${TEST_PREFIX}*)
        add_action "ovs-vsctl --if-exists del-br '$bridge'" "OVS bridge $bridge"
        ;;
      br-int|br-ex)
        if [ "$REMOVE_BRIDGES" = true ] && ovs_marked "$bridge"; then
          add_action "ovs-vsctl --if-exists del-br '$bridge'" "marked OVS bridge $bridge"
        else
          echo "skipped OVS bridge $bridge: protected bridge; use --remove-bridges if it is project-owned"
        fi
        ;;
    esac
  done < <(ovs-vsctl list-br 2>/dev/null || true)
else
  echo "skipped OVS bridge cleanup: ovs-vsctl not found"
fi

if has_cmd ip; then
  while IFS= read -r ns; do
    case "$ns" in
      ${PROJECT_PREFIX}*|${TEST_PREFIX}*|qrouter-${TEST_PREFIX}*|qdhcp-${TEST_PREFIX}*)
        add_action "ip netns delete '$ns'" "network namespace $ns"
        ;;
    esac
  done < <(ip netns list 2>/dev/null | awk '{print $1}' || true)

  while IFS= read -r iface; do
    case "$iface" in
      ${PROJECT_PREFIX}*|${TEST_PREFIX}*|${TAP_PREFIX}*|${TEST_TAP_PREFIX}*)
        add_action "ip link delete '$iface'" "link $iface"
        ;;
    esac
  done < <(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1 || true)
else
  echo "skipped ip cleanup: ip command not found"
fi

if has_cmd ovn-nbctl; then
  while IFS= read -r ls; do
    [ -z "$ls" ] && continue
    add_action "ovn-nbctl --if-exists ls-del '$ls'" "OVN logical switch $ls"
  done < <(ovn-nbctl --bare --columns=name list logical_switch 2>/dev/null | grep -E "^(${PROJECT_PREFIX}|${TEST_PREFIX})" || true)

  while IFS= read -r ls; do
    [ -z "$ls" ] && continue
    add_action "ovn-nbctl --if-exists ls-del '$ls'" "marked OVN logical switch $ls"
  done < <(ovn-nbctl --bare --columns=name find logical_switch external_ids:cloudx=true 2>/dev/null || true)

  while IFS= read -r lr; do
    [ -z "$lr" ] && continue
    add_action "ovn-nbctl --if-exists lr-del '$lr'" "OVN logical router $lr"
  done < <(ovn-nbctl --bare --columns=name list logical_router 2>/dev/null | grep -E "^(${PROJECT_PREFIX}|${TEST_PREFIX})" || true)

  while IFS= read -r uuid; do
    [ -z "$uuid" ] && continue
    add_action "ovn-nbctl destroy DHCP_Options '$uuid'" "OVN DHCP options $uuid"
  done < <(ovn-nbctl --bare --columns=_uuid find DHCP_Options external_ids:cloudx=true 2>/dev/null || true)

  while IFS= read -r pg; do
    [ -z "$pg" ] && continue
    add_action "ovn-nbctl --if-exists pg-del '$pg'" "OVN security group port group $pg"
  done < <(ovn-nbctl --bare --columns=name find port_group external_ids:cloudx=true 2>/dev/null || true)
else
  echo "skipped OVN logical cleanup: ovn-nbctl not found on host"
fi

if [ "${#actions[@]}" -eq 0 ]; then
  echo "No mini OpenStack lab network resources found."
  exit 0
fi

echo "Resources selected for cleanup:"
for description in "${descriptions[@]}"; do
  echo " - $description"
done

if [ "$DRY_RUN" = true ]; then
  echo "Dry-run mode: no changes made."
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

echo "Network cleanup complete."
