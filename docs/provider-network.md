# Provider Network

Provider NIC and bridge wiring are host-level configuration, not day-to-day user CLI input.

Environment (read by `networking-api`, see `services/networking/app/main.py::external_network_defaults`):

```env
PROVIDER_INTERFACE=enp0s8
PROVIDER_BRIDGE=br-ex
PROVIDER_PHYSNET=physnet1
PROVIDER_CIDR=192.168.18.0/24
PROVIDER_GATEWAY=192.168.18.90
PROVIDER_ALLOCATION_START=192.168.18.200
PROVIDER_ALLOCATION_END=192.168.18.220
```

`PROVIDER_CIDR`/`PROVIDER_GATEWAY`/`PROVIDER_ALLOCATION_START`/`PROVIDER_ALLOCATION_END` are
optional. If unset, `networking-api` derives CIDR and gateway from `INTERNAL_VIP_ADDRESS` as
`network_address + 1` — which is only safe on an isolated host-only network. **Set them
explicitly whenever `PROVIDER_INTERFACE` is bridged onto a physical LAN**, so the OVN
router's external port doesn't collide with the real LAN gateway (see
`docs/troubleshooting.md#gateway-ip-collides-with-the-real-lan-gateway-bridged-provider-network`).

Host bootstrap is explicit and dry-run by default:

```bash
sudo scripts/bootstrap-provider-network.sh --dry-run
sudo scripts/bootstrap-provider-network.sh --force
```

If the provider NIC currently owns an IP address and this host design requires moving that IP to `br-ex`, use `--move-ip --force` only from a safe console. Moving host IPs can interrupt connectivity.

External network creation is a manual step — `networking-api` does **not** auto-create
`public-net` at startup:

```bash
cloudx network create public-net --external
cloudx subnet create public-subnet --network public-net --cidr 192.168.18.0/24 \
    --gateway 192.168.18.90 --no-dhcp \
    --allocation-start 192.168.18.200 --allocation-end 192.168.18.220
```

`network create --external` reads the `PROVIDER_*` env values above for its OVN localnet
port and bridge mapping; it does not accept a NIC argument. The `--gateway`/`--cidr` passed
to `subnet create` are stored on the `Subnet` row for IPAM but do **not** retroactively
change the network's OVN gateway IP — that is fixed at `network create` time from
`PROVIDER_GATEWAY`. If you need a different gateway after the fact, delete and recreate the
network (see the troubleshooting section above for the full recovery sequence, including
`lrp-set-gateway-chassis`).
