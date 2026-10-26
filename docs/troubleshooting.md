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
sudo journalctl -u cloudx-compute-worker -n 100 --no-pager
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

If create fails with `Cannot find device "tap-mini-..."`, restart the worker after updating the packages:

```bash
sudo systemctl restart cloudx-compute-worker cloudx-compute-api
compute host check
```

`compute host check` should report `tun_available: true`; `/dev/net/tun` must exist on the compute node and the worker must run on the host (the `cloudx-compute-worker` unit does).

If create fails with `"dev" not a valid ifname`, the worker is still running code with the old 17-character tap name format. Restart `cloudx-compute-worker` after updating; valid Linux interface names are limited to 15 characters, and current tap names use the form `tap-mini-xxxxxx`.

If create fails with `The tap-mini-... interface already exists`, restart `cloudx-compute-worker` after updating the code. The domain XML must set `managed="no"` on the pre-created tap device so libvirt reuses it instead of trying to create it again:

```bash
sudo systemctl restart cloudx-compute-worker cloudx-compute-api
compute server delete vm1
```

## Server Goes ERROR

Inspect:

```bash
compute server show vm1
sudo journalctl -u cloudx-compute-api -u cloudx-compute-worker -n 200 --no-pager
virsh -c qemu:///system list --all
```

If the error says `Cannot access storage file ... seed.iso: No such file or directory`, make sure the host directories exist and restart the services:

```bash
sudo scripts/prepare-host-libvirt.sh --create-br-int
sudo systemctl restart cloudx-compute-worker cloudx-compute-api cloudx-block-store-api
```

The paths under `/var/lib/cloudx/compute/instances`, `/var/lib/cloudx/compute/cache`, and `/var/lib/cloudx/volumes` must exist on the node that runs the worker.

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

### VM cannot reach `http://169.254.169.254`

Known limitation. The metadata path is a host-level setup, not an agent per compute node: `metadata-proxy` listens on port 8776 of the host, and `scripts/setup-metadata-route.sh` adds an `iptables` redirect for `169.254.169.254:80` on that host plus host routes back to the VM subnets through the OVN router's external address (`metadata-proxy.md`). It therefore works for VMs on a single host that does both, and a VM on any other compute node times out (`wget: download timed out`).

This does not stop VMs from booting. SSH keys and user-data are delivered through the config drive (NoCloud), and CirrOS reads them from there; the metadata endpoint is only needed by images that cannot read a config drive. Use the config drive or the host debug URL above when validating metadata content.

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

### Floating IP does not answer ARP (router gateway on the wrong chassis)

The router's external port is pinned to one OVN gateway chassis. That chassis must have `ovn-cms-options=enable-chassis-as-gw`
and an `ovn-bridge-mappings` entry for the provider network (`physnet1:br-ex`), which on a multi-node install means the
infra nodes and not the compute nodes. Check where a router's gateway is and which chassis can carry it:

```bash
sudo ovn-nbctl lrp-get-gateway-chassis lrp-<router>-external
sudo ovn-sbctl --columns=name,hostname,other_config list chassis | grep -E 'hostname|ovn-cms-options|ovn-bridge-mappings'
sudo ovn-sbctl --bare --columns=chassis find port_binding logical_port=cr-lrp-<router>-external
```

If the gateway names a compute node, or a chassis name that no longer exists in `ovn-sbctl list chassis`, move it
(the higher priority wins; remove the wrong entry afterwards):

```bash
sudo ovn-nbctl lrp-set-gateway-chassis lrp-<router>-external <chassis-name> 20
sudo ovn-nbctl lrp-del-gateway-chassis lrp-<router>-external <wrong-chassis-name>
```

Routers created by an older build were pinned to the first chassis in the Southbound DB; recreate them or move the gateway as above.

### Chassis renamed after Open vSwitch restarted

`ovs-ctl` rewrites `external_ids:system-id` from `/etc/openvswitch/system-id.conf` every time Open vSwitch starts. Older
`cloudx-setup-compute` runs set the node name only with `ovs-vsctl`, so a restart (an unattended upgrade is enough) turned the
chassis name into a random UUID and left gateway entries pointing at a chassis that no longer exists. Check each node:

```bash
cat /etc/openvswitch/system-id.conf
sudo ovs-vsctl get open_vswitch . external_ids:system-id
```

If the two differ, the next Open vSwitch restart will rename the chassis. Make the intended name permanent first; when the value
in the database already is the node name this changes nothing that is running:

```bash
echo <node-name> | sudo tee /etc/openvswitch/system-id.conf
```

If the chassis was already renamed, either keep the new name (the two values agree, nothing more to do except repointing
gateways as above) or go back to the node name: write it to both places, restart `ovn-controller`, and delete the stale row
on a node with OVN central (`sudo ovn-sbctl chassis-del <old-name>`). Restarting `ovn-controller` on a node with running VMs
can interrupt their network for a moment; use a maintenance window.

### Provider network overlaps the Docker bridge

Docker's default bridge `docker0` takes `172.17.0.1/16`, even while it is down with no containers. If the provider network uses
`172.17.0.0/16` (or contains it), on every host with Docker:

- the host treats `172.17.0.1` as its own address, so pinging the upstream gateway from that host reaches the host itself;
- the connected route `172.17.0.0/16 dev docker0` (shown as `linkdown`) is found before a route through `br-ex`, so `ssh <floating-ip>` from the host fails with `No route to host` while `ping -I br-ex` works.

Pick a provider CIDR outside `172.17.0.0/16`. If you must keep it, move Docker's bridge in `/etc/docker/daemon.json`
(`{"bip": "10.200.0.1/24"}`, or `{"bridge": "none"}` as the container service already requires) and restart Docker. As a
workaround on one host, ping with `ping -I br-ex <ip>` and add a more specific route: `sudo ip route add <ip>/32 dev br-ex`.

### VM has no outbound access (floating IP or router SNAT)

First find where the packet stops. `ovn-trace` shows the logical path; capture on the **physical uplink** of the gateway chassis, not
on `br-ex`: the internal `br-ex` port only sees traffic addressed to the host itself.

```bash
# on the VM: ping a public address; on the gateway chassis (see "Floating IP does not answer ARP" to find it):
sudo tcpdump -nn -e -i <PROVIDER_IF> 'icmp or arp'
```

- Requests leave with the floating IP (or the router's external address) as source and the upstream gateway's MAC as destination, but no reply comes back: CloudX and OVN are fine and the problem is upstream. The provider range must be routed or NATed by the gateway, and the uplink must accept frames from the router's own MAC address. On a virtualization host (Proxmox, VMware, libvirt) the virtual NIC's MAC or anti-spoofing filter can drop frames whose source MAC is not the NIC's own; disable that filter on the provider NIC. The Proxmox cause is the usual suspect but has not been confirmed on the lab, where only the missing replies were observed.
- No ARP for the gateway at all: check `ovn-nbctl lr-route-list <router>` (default route via the provider gateway) and that the gateway chassis has the `physnet1:br-ex` mapping.
- Requests never reach the uplink: use `ovn-trace` on the VM's logical switch to see where the packet is dropped (security group egress ACLs, a missing router port).

### Gateway IP collides with the real LAN gateway (bridged provider network)

If the provider network's `PROVIDER_INTERFACE` is a **bridged** adapter onto a physical LAN
(not an isolated host-only network), `networking-api` used to derive the OVN router's
external gateway IP as `network_address + 1` of `INTERNAL_VIP_ADDRESS`. On a `/24` LAN this
is almost always `.1` — which is already the real router's address. Symptoms:

- ARP for the floating IP intermittently resolves to the physical router's MAC instead of
  the OVN router port's MAC.
- Instances boot but hang retrying `169.254.169.254` metadata forever (`failed N/20`).
- `ping <floating-ip>` from the host times out even though NAT/ACL flows show hits.

Fix: set explicit provider network keys in `/etc/cloudx/networking-api/networking-api.conf` (read by
`networking-api`) to an address range that does **not** overlap anything else on the physical LAN:

```ini
[DEFAULT]
provider_cidr = 192.168.18.0/24
provider_gateway = 192.168.18.90        # any free host on the LAN, NOT the real gateway
provider_allocation_start = 192.168.18.200
provider_allocation_end = 192.168.18.220
```

Then `sudo systemctl restart cloudx-networking-api` and recreate `public-net`/`public-subnet`/
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

If attach fails with `Bus 'pci.0' does not support hotplugging`, update and restart `compute-worker`, then recreate the VM. New domains include a `virtio-scsi` controller and block-store volumes attach as SCSI disks for hotplug support:

```bash
sudo systemctl restart cloudx-compute-worker cloudx-compute-api cloudx-block-store-api
compute server delete vm1
compute server create vm1 --image cirros --flavor tiny --network private-net --key-name mykey
block-store volume attach vol1 --server vm1
```

If a previous server delete left a volume stuck as `in-use`, repair only the block-store attachment metadata:

```bash
block-store volume force-detach vol1
```
