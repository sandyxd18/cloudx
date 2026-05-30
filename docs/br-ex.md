# br-ex

`br-ex` is the OVS bridge for the provider/external network.

Prepare it with:

```bash
sudo scripts/bootstrap-provider-network.sh --dry-run
sudo scripts/bootstrap-provider-network.sh --force
```

The script is idempotent:

- creates `br-ex` if missing
- attaches `PROVIDER_INTERFACE` if not already attached
- sets `ovn-bridge-mappings`
- does not move host IPs unless `--move-ip` is passed

Cleanup is conservative:

```bash
sudo scripts/cleanup-provider-network.sh --dry-run
sudo scripts/cleanup-provider-network.sh --force
```

It does not remove `br-ex` or detach the provider NIC unless explicit flags are passed.
