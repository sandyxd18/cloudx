#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Set up the ssh trust that live migration needs between compute nodes
# ==============================================================================
# Live migration moves a running VM over a libvirt connection of the form qemu+ssh://root@NODE/system, which
# cloudx-compute-worker (root) opens from the source node to the destination node. This script gives every compute
# node its own key and lets every node log in as root on every other node with it. Run it from a machine that can
# ssh to all the nodes as a user with sudo (default: ubuntu).
#
#   scripts/setup-migration-ssh.sh [--dry-run] NODE...
#
# For each NODE it
#   1. creates /root/.ssh/cloudx_migration (ed25519, no passphrase) when it does not exist yet;
#   2. adds the public key of each node to /root/.ssh/authorized_keys of every other node (no duplicates);
#   3. adds the host keys of every other node (ssh-keyscan) to /root/.ssh/known_hosts of the node;
#   4. writes a Host block per other node in /root/.ssh/config that uses /root/.ssh/cloudx_migration.
# It can be run again: what exists is left alone. NODE is a name or address that this machine and the other nodes can
# resolve and reach (it is also what ends up in known_hosts and config, so use the same name as live_migration_host).
#
# Options:
#   --dry-run   print what would be done and run no ssh at all
#   --user U    ssh user on the nodes, must have sudo without a password prompt (default ubuntu, or $SSH_USER)
#
# Security: afterwards every compute node has root access to every other compute node. See docs/live-migration.md.
# ==============================================================================

SSH_USER="${SSH_USER:-ubuntu}" DRY=0 NODES=()
KEY=/root/.ssh/cloudx_migration

usage() { sed -n '4,27p' "$0"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY=1; shift ;;
        --user) SSH_USER="${2:?--user needs a value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "unknown option: $1" >&2; echo "usage: $0 [--dry-run] NODE..." >&2; exit 2 ;;
        *) NODES+=("$1"); shift ;;
    esac
done

if (( ${#NODES[@]} < 2 )); then
    echo "usage: $0 [--dry-run] [--user USER] NODE NODE..." >&2
    echo "at least two nodes are needed (see --help)" >&2
    exit 2
fi
for node in "${NODES[@]}"; do
    [[ "$node" =~ ^[A-Za-z0-9_.:-]+$ ]] || { echo "invalid node name: $node" >&2; exit 2; }
done
[[ "$SSH_USER" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "invalid ssh user: $SSH_USER" >&2; exit 2; }

# Run a script (stdin) as root on a node; the arguments reach it as $1, $2, ...
remote() {
    local node="$1" args=() a; shift
    for a in "$@"; do args+=("$(printf '%q' "$a")"); done
    ssh -o BatchMode=yes "${SSH_USER}@${node}" "sudo bash -s -- ${args[*]:-}"
}

# 1. key on the node; prints its public key
ENSURE_KEY='set -euo pipefail
key=/root/.ssh/cloudx_migration
install -d -m 700 /root/.ssh
[[ -f $key ]] || ssh-keygen -q -t ed25519 -N "" -C "cloudx-migration@$(hostname)" -f "$key"
cat "$key.pub"'

# 2-4. trust on the node: $1 = public key of another node, $2 = that node
TRUST_PEER='set -euo pipefail
pub=$1 peer=$2 key=/root/.ssh/cloudx_migration
install -d -m 700 /root/.ssh
touch /root/.ssh/authorized_keys /root/.ssh/known_hosts /root/.ssh/config
chmod 600 /root/.ssh/authorized_keys /root/.ssh/known_hosts /root/.ssh/config
grep -qxF "$pub" /root/.ssh/authorized_keys || echo "$pub" >> /root/.ssh/authorized_keys
if ! ssh-keygen -F "$peer" -f /root/.ssh/known_hosts >/dev/null; then
    scanned=$(ssh-keyscan -H -T 10 "$peer" 2>/dev/null) || scanned=""
    [[ -n $scanned ]] || { echo "ssh-keyscan found no host key on $peer (is sshd reachable from here?)" >&2; exit 1; }
    echo "$scanned" >> /root/.ssh/known_hosts
fi
grep -qxF "# BEGIN cloudx-migration $peer" /root/.ssh/config || cat >> /root/.ssh/config <<CFG
# BEGIN cloudx-migration $peer
Host $peer
    User root
    IdentityFile $key
    IdentitiesOnly yes
# END cloudx-migration $peer
CFG'

if (( DRY )); then
    echo "dry run: nothing is changed and no ssh is run. Plan for: ${NODES[*]}"
    for node in "${NODES[@]}"; do
        echo "+ ssh ${SSH_USER}@${node} sudo: create ${KEY} (ed25519, no passphrase) unless it exists, print ${KEY}.pub"
    done
    for node in "${NODES[@]}"; do
        for peer in "${NODES[@]}"; do
            [[ "$peer" == "$node" ]] && continue
            echo "+ ssh ${SSH_USER}@${node} sudo: add the public key of ${peer} to /root/.ssh/authorized_keys (if missing)," \
                 "ssh-keyscan ${peer} into /root/.ssh/known_hosts (if missing)," \
                 "add 'Host ${peer}' with IdentityFile ${KEY} to /root/.ssh/config (if missing)"
        done
    done
    exit 0
fi

declare -A PUB
for node in "${NODES[@]}"; do
    echo "===> ${node}: key ${KEY}"
    PUB[$node]="$(printf '%s\n' "$ENSURE_KEY" | remote "$node" | tail -n 1)"
    [[ "${PUB[$node]}" == ssh-ed25519\ * ]] || { echo "${node}: did not get a public key back (got: ${PUB[$node]})" >&2; exit 1; }
done

for node in "${NODES[@]}"; do
    for peer in "${NODES[@]}"; do
        [[ "$peer" == "$node" ]] && continue
        echo "===> ${node}: trust ${peer} (authorized_keys, known_hosts, config)"
        printf '%s\n' "$TRUST_PEER" | remote "$node" "${PUB[$peer]}" "$peer"
    done
done

echo "Done. Check one pair by hand, for example:"
echo "  ssh ${SSH_USER}@${NODES[0]} sudo ssh -i ${KEY} root@${NODES[1]} virsh -c qemu:///system list"
