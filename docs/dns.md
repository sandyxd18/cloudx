# DNS (Designate-like, on BIND9)

The CloudX DNS service: user-owned zones and records, plus automatic names for platform resources. It covers Designate's features, not 1:1 (see the limits).

## Components (controller only)

| Unit | Purpose |
|---|---|
| `cloudx-dns-api` | API (port 8016), stores zones and records in PostgreSQL |
| `cloudx-dns-worker` | writes zone files and `named.conf`, reloads BIND, sweeps automatic names every 60 seconds |
| `cloudx-named-auth` | authoritative BIND, no recursion, `172.20.0.120` |
| `cloudx-named-resolver` | recursive BIND for tenants: internal zones + forwarder, `172.20.0.118` |

The BIND files are in `/var/lib/cloudx/dns/{auth,resolver}`. BIND must not bind `0.0.0.0` because `systemd-resolved` uses port 53 on localhost.

## Configuration (`/etc/cloudx/cloudx.conf`)

`dns_platform_domain`, `dns_public_address`, `dns_resolver_address`, `dns_upstream`, `dns_resolver_clients`. An example is in `etc/cloudx/cloudx.conf.sample`.

`dns_resolver_clients` must include the compute/gateway nodes' network: VM traffic is SNATed to the node address (seen in the lab, where the source was `172.20.0.117`, not the VM's IP).

## Zones and records

```
cloudx dns zone create example.com
cloudx dns zone show example.com          # shows the NS to enter at the registrar
cloudx dns record create example.com www A 192.0.2.10
cloudx dns record list example.com
cloudx dns zone create corp.internal --internal
cloudx dns secondary add example.com 203.0.113.5
```

- Types: A, AAAA, CNAME, MX, TXT, SRV, NS, PTR. The SOA and the NS at the apex are managed by the system.
- CNAME rules are enforced (it cannot share a name with another record, and cannot be at the apex).
- Names under `cloud.lab` are reserved for the platform.
- A zone is `PENDING` until the worker loads it, then `ACTIVE`.
- Per-project quotas: `max_zones` (10) and `max_recordsets` (200); automatic records are not counted.

## Automatic names

| Resource | Name | Served by |
|---|---|---|
| Floating IP | `<ip-dash>.compute.cloud.lab` | auth + resolver |
| Load balancer + floating IP | `<name>-<6 id>.elb.cloud.lab` | auth + resolver |
| Bucket | `<bucket>-<8 project id>.s3.cloud.lab` | auth + resolver |
| Server and LB VIP | `<name>.<project>.internal.cloud.lab` | resolver only |

The records are created by the owning service at create/delete and reconciled by the sweep. A DNS failure does not fail the creation of a resource. Automatic records are read-only (409).

A new subnet without `dns_nameservers` uses the platform resolver.

## Secondary

A public zone can have a secondary registered by IP: that server may AXFR and receives NOTIFY. CloudX is primary only. Loopback, unspecified and multicast addresses are rejected.

## Limits

No TSIG, DNSSEC, secondary-type zones, zone import, floating IP PTR, or pools/blacklists. The resolver has `dnssec-validation no`. Full details in `docs/releases/v0.8.0.md`.

## Testing

`scripts/test-dns.sh` does not exist yet; lab verification was done by hand (dig against `.120` and `.118`, AXFR from a registered secondary, a real VM for the internal names).
