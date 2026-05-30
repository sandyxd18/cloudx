# Provider Network

Provider NIC and bridge wiring are host-level configuration, not day-to-day user CLI input.

Environment:

```env
PROVIDER_INTERFACE=enp0s8
PROVIDER_BRIDGE=br-ex
PROVIDER_PHYSNET=physnet1
PROVIDER_NETWORK_NAME=public-net
PROVIDER_SUBNET_NAME=public-subnet
PROVIDER_CIDR=192.168.56.0/24
PROVIDER_GATEWAY=192.168.56.1
PROVIDER_ALLOCATION_START=192.168.56.100
PROVIDER_ALLOCATION_END=192.168.56.200
PROVIDER_DNS=8.8.8.8
PROVIDER_ENABLE_DHCP=false
```

Host bootstrap is explicit and dry-run by default:

```bash
sudo scripts/bootstrap-provider-network.sh --dry-run
sudo scripts/bootstrap-provider-network.sh --force
```

If the provider NIC currently owns an IP address and this host design requires moving that IP to `br-ex`, use `--move-ip --force` only from a safe console. Moving host IPs can interrupt connectivity.

When `networking-api` starts, it idempotently creates:

- external network `public-net`
- provider subnet `public-subnet`
- OVN localnet port
- provider bridge mapping metadata

Manual external network creation is intentionally simple:

```bash
networking network create public-net --external
```

The command uses provider env configuration; it does not accept a NIC.
