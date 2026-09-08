# Troubleshooting

## `/dev/kvm` Missing

Enable nested virtualization in the outer hypervisor and verify:

```bash
ls -l /dev/kvm
grep -E '(vmx|svm)' /proc/cpuinfo
```

## Libvirt Socket

```bash
virsh -c qemu:///system list --all
docker compose logs compute-worker
compute host check
```

If the socket is missing, restart libvirt:

```bash
sudo systemctl enable --now libvirtd
```

If `virsh` fails with `Failed to connect socket to '/var/run/libvirt/libvirt-sock': Permission denied`, add your user to the host libvirt groups, then log out and log back in:

```bash
sudo usermod -aG libvirt,kvm "$USER"
newgrp libvirt
virsh -c qemu:///system list --all
```

As a temporary check, run the console command shown by `compute server console vm1 --json` with `sudo virsh`.

## OVS/OVN Binding

```bash
ovs-vsctl br-exists br-int
ovs-vsctl show
networking ovn show
```

Each VM tap should have `external_ids:iface-id`, `attached-mac`, `vm-id`, and `iface-status=active`.

If create fails with `Cannot find device "tap-mini-..."`, rebuild/recreate the worker after updating Compose:

```bash
docker compose up -d --build compute-worker compute-api
compute host check
```

`compute host check` should report `tun_available: true`; the worker must run with `/dev/net/tun` mounted and `network_mode: host`.

If create fails with `"dev" not a valid ifname`, the worker image is still using the old 17-character tap name format. Rebuild/recreate `compute-worker`; valid Linux interface names are limited to 15 characters, and current tap names use the form `tap-mini-xxxxxx`.

If create fails with `The tap-mini-... interface already exists`, rebuild/recreate `compute-worker` after updating the code. The domain XML must set `managed="no"` on the pre-created tap device so libvirt reuses it instead of trying to create it again:

```bash
docker compose up -d --build compute-worker compute-api
compute server delete vm1
```

## Server Goes ERROR

Inspect:

```bash
compute server show vm1
docker compose logs compute-api compute-worker
virsh -c qemu:///system list --all
```

If the error says `Cannot access storage file ... seed.iso: No such file or directory`, recreate the services after switching to bind mounts and make sure the host directories exist:

```bash
sudo scripts/prepare-host-libvirt.sh --create-br-int
docker compose up -d --build compute-worker compute-api block-store-api
```

The paths under `/var/lib/mini-openstack/compute/instances`, `/var/lib/mini-openstack/compute/cache`, and `/var/lib/mini-openstack/volumes` must exist on the host, not only inside Docker volumes.

Then clean leftovers:

```bash
sudo scripts/cleanup-all.sh --dry-run
sudo scripts/cleanup-all.sh --force
```

## Metadata

Host debug path:

```bash
compute server show vm1
curl "http://localhost:8775/openstack/latest/meta_data.json?instance_id=<server-id>"
```

VM access through `http://169.254.169.254` is experimental in this single-node milestone. Use config-drive or the host debug URL when validating metadata content.

## Floating IP

Floating IP requires an external network and a router gateway:

```bash
networking network show public-net
networking router set-gateway router1 public-net
```

Host bridge preparation is explicit and dry-run by default:

```bash
sudo scripts/bootstrap-provider-network.sh --dry-run
```

### Gateway IP collides with the real LAN gateway (bridged provider network)

If the provider network's `PROVIDER_INTERFACE` is a **bridged** adapter onto a physical LAN
(not an isolated host-only network), `networking-api` used to derive the OVN router's
external gateway IP as `network_address + 1` of `INTERNAL_VIP_ADDRESS`. On a `/24` LAN this
is almost always `.1` — which is already the real router's address. Symptoms:

- ARP for the floating IP intermittently resolves to the physical router's MAC instead of
  the OVN router port's MAC.
- Instances boot but hang retrying `169.254.169.254` metadata forever (`failed N/20`).
- `ping <floating-ip>` from the host times out even though NAT/ACL flows show hits.

Fix: set explicit provider network variables in `.env` (read by `networking-api`, wired
through `docker-compose*.yml`) to an address range that does **not** overlap anything else
on the physical LAN:

```env
PROVIDER_CIDR=192.168.18.0/24
PROVIDER_GATEWAY=192.168.18.90        # any free host on the LAN, NOT the real gateway
PROVIDER_ALLOCATION_START=192.168.18.200
PROVIDER_ALLOCATION_END=192.168.18.220
```

Then `docker compose up -d --build networking-api` and recreate `public-net`/`public-subnet`/
`router1` from scratch — the gateway IP is baked into the network row at creation time and is
not corrected retroactively. After `router set-gateway`, if `get_local_chassis_id()` logged a
warning (e.g. ovn-controller had just restarted), the external port stays unbound — confirm
with `ovn-sbctl list Port_Binding cr-lrp-<router>-external` (`chassis` must be non-empty,
`up: true`) and fix manually if needed:

```bash
CHASSIS=$(ovn-sbctl --format=list --columns=name find Chassis | grep -oP '(?<=: ")[^"]+')
ovn-nbctl lrp-set-gateway-chassis lrp-<router>-external "$CHASSIS"
```

Also add the host route for the private subnet via the **OVN gateway IP**, not the real LAN
gateway:

```bash
sudo ip route add 192.168.12.0/24 via 192.168.18.90 dev br-ex
```

## Volume Attach

If attach fails with `Bus 'pci.0' does not support hotplugging`, rebuild `compute-worker` and recreate the VM. New domains include a `virtio-scsi` controller and block-store volumes attach as SCSI disks for hotplug support:

```bash
docker compose up -d --build compute-worker compute-api block-store-api
compute server delete vm1
compute server create vm1 --image cirros --flavor tiny --network private-net --key-name mykey
block-store volume attach vol1 --server vm1
```

If a previous server delete left a volume stuck as `in-use`, repair only the block-store attachment metadata:

```bash
block-store volume force-detach vol1
```
