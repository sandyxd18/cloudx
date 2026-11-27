# Metadata Proxy

`metadata-proxy` receives VM requests for metadata paths and forwards them to `metadata-api`. The proxy runs on the host network on port `8776` so the VM's source IP can be used for lookup.

Environment:

```env
METADATA_API_URL=http://metadata-api:8775
NETWORKING_API_URL=http://networking-api:8001
METADATA_PROXY_PORT=8776
```

Proxy lookup:

1. Take the request's source IP or `X-Forwarded-For`.
2. Query `networking-api /ports`.
3. Match `fixed_ip` or `ip_address`.
4. Forward to metadata-api with `X-Forwarded-For`, `X-Networking-Port-ID`, and `X-Instance-ID`.

To set up the metadata host route:

```bash
sudo scripts/setup-metadata-route.sh --dry-run
sudo scripts/setup-metadata-route.sh --force
```

To clean up the metadata rule:

```bash
sudo scripts/cleanup-metadata-route.sh --dry-run
sudo scripts/cleanup-metadata-route.sh --force
```

The rule only targets `169.254.169.254:80` to the metadata-proxy port. This is still single-node lab behaviour, not the full CloudX metadata agent.

## What a request goes through

A VM sends its request to `169.254.169.254` through its router. The router needs a route for that address whose next hop is the host that runs `metadata-proxy`, with an address on the provider network (`INTERNAL_VIP_ADDRESS` of networking-api makes it add the route when an interface is added to a router; otherwise add `169.254.169.254/32` with that next hop to the router's `routes`). On that host the `iptables` rule above sends the request to the proxy, and the host needs a route back to the VM's subnet through the router (`setup-metadata-route.sh` adds it). Only VMs whose traffic leaves through the node of that host are served.

The proxy finds the VM by the source address, so the router must not translate it. networking-api therefore adds the address set `cloudx_metadata` (`169.254.169.254`) to the OVN NAT database and lists it as `exempted_ext_ips` on every NAT row of every router: the subnet `snat` rows and the `dnat_and_snat` row of a floating IP. It does this when a row is made and again each time networking-api starts, so routers and floating IPs from before the change are covered after an upgrade. Before this, the proxy saw the router's external address (or the floating IP) and answered `404` to every VM. To check a router, `ovn-nbctl list nat` shows `exempted_ext_ips` with the address set's UUID on each row.

## Overlapping address ranges

The proxy identifies a VM by its source address. If two live instances (for example in two projects) hold the same address, the address does not say which VM is asking, so the proxy answers `404` and does not forward the request; `compute` answers `404` to a lookup by address in the same case. Those VMs read their SSH keys and user-data from the config drive.
