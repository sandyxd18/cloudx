# BGP dynamic routing (BIRD) — design

Status: approved by the user (chat, 2026-10-12). Spike: `docs/superpowers/plans/bgp-spike-notes.md` (two BIRD 2.14 in two netns, Established, MD5 tested, `birdc configure` without dropping the session, no AppArmor profile).

## Goal
The `networking_bgp_peers` table and its CRUD already exist, but there is no daemon: `sync-config` only produces text. This phase makes it real: a real eBGP session to an external peer, `advertised_prefixes` are announced, and the learned routes go into the OVN router.

## Architecture (the same pattern as VPNaaS)
- **Speaker** = one `bird` per router, in the netns `qbgp-<short>` on the router's gateway chassis host, managed by `bgp-worker` (compute and storage, polling every 5 s, `InternalAuth`).
- One internal OVS port on `br-int` (`iface-id` = the LSP on the router's external network LS) with its own `outer_ip` from the external network pool (not the router's gateway IP). No transit network: the peer must be on the same external network L2 (no multihop).
- `short = gw_id.split("-",1)[1][:8]` (ID `bgpsp-<12 hex>`).
- All peers on the same router share one BIRD. `local_as` must be the same among the peers of one router (different → 409).
- A router without an external gateway → 400 when creating a peer.

## Data
- New table `networking_bgp_speakers`: `id, router_id (unique), project_id, host, outer_network_id, outer_ip, outer_mac, status, created_at`. Created with the first peer on a router, deleted with the last peer (the OVN objects are cleaned up too, best-effort rollback as in VPN).
- New table `networking_bgp_learned_routes`: `id, peer_id, prefix, nexthop`. Used by the API to know which OVN routes are its own.
- `BGPPeer` gets `speaker_id` (no FK, like the VPN `gateway_id`) and `status_reason`. `status` becomes `down | established | error`.
- Migration `networking_0007` (idempotent, the downgrade only drops the tables).

## Worker (`services/bgp_worker`)
- Per speaker: netns `qbgp-<short>`, an OVS port with `external_ids:cloudx_bgp=<id>`, `bird -c <dir>/bird.conf -s <dir>/bird.ctl -P <dir>/bird.pid` with `<dir> = /var/lib/cloudx/bgp/<id>/`, waiting until the socket exists. `bird.conf` is written 0600 by the pure function `shared/bird_cfg.py:render()`; `birdc configure` only when the sha256 changes.
- Configuration: `router id = outer_ip`; `protocol device`; a static protocol (blackhole) for the `advertised_prefixes` of all peers; one `protocol bgp` per peer (`local outer_ip as local_as; neighbor peer_ip as remote_as; password`), export only the static routes with `bgp_next_hop = router_ext_ip` (the router's gateway IP, so traffic enters the OVN router, not the netns), import all routes except the default and prefixes that override a router subnet.
- Status each tick: `birdc show protocols` → `established` or `down`; learned routes through `birdc show route protocol <bgp> all` (parse `via`/`BGP.next_hop`). The result is sent to `POST /internal/bgp-agent/{host}/status` only if it changed.
- Orphans (a netns/port/dir/bird without a speaker in the list) are cleaned up. The password is never written to the log. `bird` is started detached through `shared.proc.run` with a shell, the pid taken from `bird.pid`.

## API
- `POST /bgp-peers`: validates, allocates/finds the speaker (host from `lrp-get-gateway-chassis` → SB hostname, the VPN `_get_router_host` helper is reused), creates the outer LSP, a unique `outer_ip` (avoiding FIPs, the router gateway, VPN gateways, other speakers), 409 if the pool is exhausted.
- `DELETE /bgp-peers/{id}`: delete that peer's learned routes from OVN, then the speaker if it was the last peer.
- `GET/POST /internal/bgp-agent/{host}[/status]` (`InternalAuth`): speaker data + active peers (`is_active`) + prefixes; the status accepts `{peers: {id: {status, status_reason, routes: [{prefix, nexthop}]}}}`.
- Route reconciliation: for each peer, the difference between `routes` and `networking_bgp_learned_routes`; added → `lr-route-add <router> <prefix> <nexthop>`, gone → `lr-route-del`. The next hop must be in the external network's subnet; otherwise it is ignored. A `down` peer → all its routes are withdrawn.
- `sync-config` produces the BIRD configuration with the password masked (`render(mask=True)`); the response key stays `frr_config` and `bird_config` is added, so the old CLI/dashboard do not break.
- The `cloudx bgp-peer` CLI shows `status`, `status_reason`, `outer_ip`, and the learned routes.

## Tests
- Unit (mock `ovn_cli`, `run`): OVN objects created and rolled back, 400/409, route reconciliation (add/remove/withdrawn when down), `render()` (the password only in the 0600 file), the exact worker commands, orphans, status only on change, the password absent from logs/argv. Internal endpoints through `TestClient` with `INTERNAL_TOKEN`. A mutation check for each invariant.
- Live (`cloudx-net-ops`, without manual adjustments): a test router `zz-bgp-r` with a gateway to `public-net`; the "ISP" = a second BIRD in another netns on the same host, placed on the external network's L2 (an OVS port on br-int with its own LSP, created by the test script). Evidence: Established on both sides, the route from the ISP appears in `lr-route-list`, a VM pings an address behind the ISP through that route, the route disappears when the ISP withdraws the prefix or the peer is deleted, the ISP receives the `advertised_prefixes` with the router's IP as next hop. Clean-up and a before/after comparison of the `router1` state.
- The risk proven in the live test: inbound traffic from the ISP to private prefixes passes the router's SNAT; if the reply is not symmetric, the result is recorded as a limitation (advertising still works for public prefixes/FIPs).

## Limitations (for the release notes)
- IPv4 eBGP only, the peer must be on the same L2 as the external network (no multihop).
- One host per speaker, no failover.
- MD5 passwords are stored in plaintext.
- No BFD, IPv6, custom route-maps, graceful restart; export stays `advertised_prefixes` only.
- Default routes and prefixes that override a router subnet from the peer are ignored.
- Inbound to private prefixes that the router SNATs depends on reply symmetry (see the risk).
- `sync-config` no longer produces FRR; the `frr_config` key is kept for compatibility but holds the BIRD configuration.

## Dependencies/installation
`bird2` in `scripts/setup-compute-node.sh` (disable the distro's `bird` unit), the unit `cloudx-bgp-worker`, `/etc/cloudx/bgp-worker/bgp-worker.conf` (like vpn-worker). Docs: the BGP page (written in Indonesian at the time; now English), remove the "no BGP transit" claim.
