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

## Overlapping address ranges

The proxy identifies a VM by its source address. If two live instances (for example in two projects) hold the same address, the address does not say which VM is asking, so the proxy answers `404` and does not forward the request; `compute` answers `404` to a lookup by address in the same case. Those VMs read their SSH keys and user-data from the config drive.
