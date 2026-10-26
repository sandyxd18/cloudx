# Real IPsec site-to-site VPNaaS: design

Date: 2026-10-11. Phase 3, item 3 (after volume replication and VPC peering).

## Goal

Today VPNaaS only stores records (IKE policy, IPsec policy, site connection) and produces `ipsec.conf` text.
No tunnel runs. Target: a VPN connection really comes up, and traffic between the local router's subnets and
`peer_cidrs` goes through an encrypted IPsec tunnel.

Success criteria (live, in the lab):
- Two CloudX routers (A = `router1`, B = `zz-vpn-r`, both with an external gateway to `public-net`) connect to
  each other over `public-net`. Each has a mirror connection.
- A VM behind A pings a VM behind B. `tcpdump` on `public-net` shows ESP, not ICMP.
- Delete the connection: the ping fails again, and the netns, OVS ports, OVN objects and the daemon are cleaned up.
- The connection status in the API follows reality: `PENDING_CREATE`, `ACTIVE`, `DOWN`.

Assumption: the lab has no real remote site, so the remote side is a second CloudX router. `vps-an` is not used.

## Out of scope (goes into the limitations in the release notes)

- PSK only. No X.509 certificates.
- One host per gateway, no failover.
- The PSK is stored in plaintext in the DB (as it is today).
- No remote-access VPN. No BGP over IPsec.
- No configurable dead-peer detection. The strongSwan default (`dpd_delay`) is used.

## Decision

The IPsec endpoint runs in a **namespace per router** (chosen by the user). Every side of the namespace is bound
to OVN with the `lb-worker` pattern (an internal port on `br-int` with `external_ids:iface-id`), so OVN handles
L2/L3 and the host gets no NAT or extra rules.

Rejected: a veth to `br-ex` with MASQ on the host (bypasses OVN, host ARP/rules may collide), and strongSwan in
the root namespace with xfrm policy (one shared daemon, touches the host stack).

## Topology

```
local subnet VM -- router (OVN) --LRP-- LS vpn-<gw> --LSP-- [qvpn-<gw>: iface "in"]
                                                              charon (strongSwan)
public-net ----------------------LS public --LSP-- [qvpn-<gw>: iface "out", real IP from public-net]
```

- Inner side: the transit logical switch `vpn-<gw>` is connected to the router through an LRP. The transit IPs
  come from a /30 in `100.101.0.0/16` (`.1` router, `.2` namespace).
- Outer side: an LSP on the router's external network. Its IP is the local IKE address (`outer_ip`), not the
  router's gateway IP, so it does not collide with OVN's own ARP.
- The router gets a static route for `peer_cidrs` via the namespace's transit IP. The namespace gets a route to
  the local subnets via `.1`, and a default via the `public-net` gateway.
- `ip_forward=1` in the namespace.

## Components

### 1. Model and migration (`networking_0006`)

New table `networking_vpn_gateways`, one row per router:

| column | meaning |
|---|---|
| `id`, `router_id` (unique), `project_id` | identity |
| `host` | the node where the namespace runs |
| `transit_cidr` | a /30 from `100.101.0.0/16` |
| `outer_network_id`, `outer_ip` | outer network and IP (from the external subnet) |
| `inner_mac`, `outer_mac` | stable MACs (`stable_mac`) |
| `status` | `PENDING_CREATE`, `ACTIVE`, `ERROR` |

`networking_vpn_site_connections` gets the columns `gateway_id`, `status` with the values
`PENDING_CREATE|ACTIVE|DOWN|ERROR`, and `status_reason`. A gateway is created with the first connection on a
router and deleted when the last connection is gone.

### 2. Networking API (`routers/vpn.py`)

Create connection:
1. The router belongs to the caller. The router must have an external gateway, otherwise 400.
2. Reject with 409 if `peer_cidrs` overlap a router subnet, or each other.
3. Pick or create the gateway (host = the router's gateway chassis host; a new transit /30, 409 if the pool is exhausted).
4. Create the OVN objects idempotently: LS `vpn-<gw>`, router LRP, inner LSP, outer LSP, static route `peer_cidrs`.
   Failure midway: roll back the DB and delete the OVN objects already created (best-effort).
5. Status `PENDING_CREATE`. The worker changes it to `ACTIVE` or `DOWN`.

Delete connection: delete the `peer_cidrs` route; if it was the last connection on the gateway, delete the LSPs,
the LS, the LRP, and then the gateway. The worker cleans up the namespace on its next tick.

`admin_state_up=false` removes the connection from the worker's configuration (the tunnel goes down); the OVN
objects stay.

Internal endpoints (`require_internal`, token `X-Internal-Token`):
- `GET /internal/vpn-agent/{host}`: the host's gateways with their connections, policies and PSKs.
- `POST /internal/vpn-agent/{host}/status`: `{connection_id: ACTIVE|DOWN, reason}`.

`POST /vpn/site-connections/{id}/config` stays (the same rendered text as the worker uses, with the PSK masked).

### 3. Configuration rendering (`shared/swanctl_cfg.py`)

A pure function `render(gateway, connections) -> str` produces `swanctl.conf` (IKEv2, PSK, proposals from the
IKE/IPsec policies, `local_addrs = outer_ip`, `remote_addrs = peer_address`, `local_ts` = the router's subnets,
`remote_ts = peer_cidrs`, `start_action = start`). Values are already restricted to a safe alphabet by the
`VPNSiteConnectionIn` schema. The `/config` endpoint uses this function too.

### 4. `vpn-worker` (`services/vpn_worker`)

Runs on compute and storage nodes. Config from `/etc/cloudx/vpn-worker/vpn-worker.conf` (`node_name`, not an
environment variable in systemd). Polls `/internal/vpn-agent/<host>` every 5 seconds. For each gateway:
- netns `qvpn-<gw8>`, two internal ports on `br-int` (`iface-id` = the names of the inner and outer LSPs), MAC and IP from the API.
- one `charon` per namespace with its own `strongswan.conf`, directory and vici socket in
  `/var/lib/cloudx/vpn/<gw>/`, started through `ip netns exec`.
- `swanctl.conf` is written with mode 0600, then `swanctl --load-all`. A configuration hash avoids needless reloads.
- Every tick: `swanctl --list-sas` decides `ACTIVE` if a child SA is installed, otherwise `DOWN`. Status changes
  are reported to the API.
- Namespaces, OVS ports, directories and `charon` processes that are no longer wanted are cleaned up.
- The PSK never reaches the log.

The systemd unit `cloudx-vpn-worker` on compute and storage nodes. The packages `strongswan-swanctl` and
`charon-systemd` (without starting the distro's daemon) are added to the compute and storage install scripts.

### 5. CLI and docs

The `vpn` CLI already exists. Add the columns `status`, `outer_ip` in `show`, and `status_reason`. Docs updated:
remove the control-plane-only claim, explain the topology, the limitations, and how to diagnose
(`swanctl --list-sas` inside `ip netns exec qvpn-*`).

## Error handling

- OVN fails during create: full rollback, 502 following the `ovn_cli` pattern.
- The worker cannot start `charon` or load the configuration: the connection becomes `ERROR` with a
  `status_reason`, and the next tick tries again.
- The peer is unreachable: the connection is `DOWN`, strongSwan keeps trying (`start_action = start`).
- The gateway host is down: the connection stays `ACTIVE` in the DB until the worker reports. There is no failover (a limitation).

## Testing

- Render unit tests: `swanctl.conf` (proposals, TS, PSK) through asserts.
- API unit tests with `ovn_cli.ovn` mocked: the OVN objects created (LS, LRP, two LSPs, route), 400 without an
  external gateway, 409 for overlapping CIDRs, rollback cleans the objects, the last gateway is deleted with the
  last connection, the transit /30 is unique per gateway.
- Worker unit tests with `run` mocked: the order of the netns/port/charon commands, no reload when the hash is
  the same, cleanup of orphans, status reported only when it changes.
- Mutation: proof that the tests fail if the overlap guard and the rollback cleanup are removed.
- Live: the success-criteria scenario above, plus a cleanliness check (`ip netns`, `ovs-vsctl show`, `ovn-nbctl`).

## Risk

Two separate `charon` instances in two netns on the same host, each with its own `strongswan.conf`, have not been
tried in the lab. The first step of the plan is a short spike to confirm it before the worker is written. If it
fails, the fallback: one `charon` per host, one swanctl connection per namespace, with `ip xfrm` in the namespace.
