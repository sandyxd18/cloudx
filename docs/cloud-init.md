# Cloud-Init

The lab uses NoCloud/config-drive first. Metadata HTTP at `169.254.169.254` is planned for a later phase.

Per instance:

```text
/var/lib/mini-openstack/compute/instances/<server_id>/seed.iso
/var/lib/mini-openstack/compute/instances/<server_id>/cloud-init/meta-data
/var/lib/mini-openstack/compute/instances/<server_id>/cloud-init/user-data
/var/lib/mini-openstack/compute/instances/<server_id>/cloud-init/network-config
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
