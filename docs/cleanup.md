# Cleanup

Cleanup scripts are conservative and log selected resources before deletion.

```bash
scripts/cleanup-compute.sh --dry-run
scripts/cleanup-network.sh --dry-run
scripts/cleanup-all.sh --dry-run
scripts/cleanup-provider-network.sh --dry-run
sudo scripts/cleanup-all.sh --force
sudo scripts/cleanup-all.sh --force --with-volumes
```

## Compute Cleanup

`cleanup-compute.sh` targets:

- libvirt domains with prefix `mini-os-`
- libvirt domains with prefix `test-mini-`
- OVS ports with prefix `tap-mini-`
- OVS ports with prefix `tap-test-`
- tap interfaces with prefix `tap-mini-`
- tap interfaces with prefix `tap-test-`
- instance directories under `/var/lib/mini-openstack/compute/instances`

Running domains are destroyed before undefine. Managed save and NVRAM are removed when libvirt supports it.

## Network Cleanup

`cleanup-network.sh` targets:

- OVS ports with prefix `tap-mini-`
- OVS ports with prefix `tap-test-`
- project-prefixed links/namespaces
- marked OVN logical switches/routers
- project-prefixed OVN resources
- marked OVN DHCP options

It does not delete important host interfaces. `cleanup-network.sh` defaults to dry-run. `br-int`/`br-ex` are not removed unless they are marked by the project and you pass `--remove-bridges --force`.

## Provider Cleanup

`cleanup-provider-network.sh` is separate because `br-ex` and `PROVIDER_INTERFACE` are host-level resources. It clears the bridge mapping by default, but does not remove `br-ex` or detach the provider NIC unless explicit flags are used:

```bash
sudo scripts/cleanup-provider-network.sh --dry-run
sudo scripts/cleanup-provider-network.sh --force
sudo scripts/cleanup-provider-network.sh --force --detach-provider-interface --remove-provider-bridge
```

## Integration Tests

`scripts/test-real-vm.sh` uses `trap cleanup EXIT`, creates resources with prefix `test-mini-`, deletes the server, and calls cleanup at the end.
