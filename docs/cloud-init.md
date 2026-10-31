# Cloud-Init

The lab uses NoCloud/config-drive first. Metadata HTTP at `169.254.169.254` is planned for a later phase.

Per instance:

```text
/var/lib/cloudx/compute/instances/<server_id>/seed.iso
/var/lib/cloudx/compute/instances/<server_id>/cloud-init/meta-data
/var/lib/cloudx/compute/instances/<server_id>/cloud-init/user-data
/var/lib/cloudx/compute/instances/<server_id>/cloud-init/network-config
```

`meta-data` includes `instance-id` and `local-hostname`.

`user-data` includes:

- hostname through metadata
- optional SSH authorized key
- `ssh_pwauth: false`
- root disable hint
- growpart/rootfs resize hints
- `package_update: false`

`network-config` uses the fixed IP, CIDR, gateway, and DNS from networking-api when available. If no fixed IP exists, it falls back to DHCP.

A server created on several networks (`networks` in the create request) has one port, and so one NIC, per network. The first network's port is `eth0` and carries the default route; the others are `eth1`, `eth2`, ... in the order given. Each NIC is matched by its MAC address and gets the addresses networking-api allocated, as static configuration, with no default route of its own so that it cannot compete with `eth0`'s; a port with no address asks DHCP. The extra NICs are plugged when the server is built, also when it is evacuated to another host, and their taps are removed when it is deleted. A port attached later with `cloudx server port add` is hot-plugged and is not in `network-config`: configure it inside the guest.
