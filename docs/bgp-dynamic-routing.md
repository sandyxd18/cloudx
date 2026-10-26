# BGP dynamic routing (BIRD)

Real eBGP sessions between a CloudX router and an external peer. One BIRD 2 daemon per router runs in the netns `qbgp-<short>` on the router's gateway chassis host, managed by `cloudx-bgp-worker`.

## Topology

```
external peer ---- external network L2 ---- br-int (port bgp<short>-out, outer_ip)
                                                  |
                                          netns qbgp-<short>: bird
                                                  |
                                    OVN router (advertised next-hop = router gateway IP)
```

- One speaker per router, created with the first peer and removed with the last one.
- The speaker uses its own `outer_ip` from the external network pool (not the router's gateway IP). The peer must be on the same external network L2 (no multihop).
- All peers on one router share one BIRD; `local_as` must be the same (otherwise 409). A router without an external gateway: 400.

## Behaviour

- Advertised: only `advertised_prefixes`, with the router gateway IP as next-hop so traffic enters the OVN router, not the netns.
- Learned: routes from the peer are added to the OVN router with `lr-route-add` and recorded in `networking_bgp_learned_routes`. When a peer goes `down` or is deleted, all of its routes are withdrawn. Other static routes on the router are not touched.
- Ignored: the default route (`0.0.0.0/0`), prefixes equal to, more specific than, or a supernet of a router subnet, next-hops outside the external network's subnet, and prefixes that are already a static route on the router.
- `bird.conf` is written with mode 0600; `birdc configure` only runs when the content changes. The password never reaches the log or argv.
- Status (`down`, `established`, `error` + `status_reason`) and learned routes are reported by the worker every 5 seconds, only when they change.

## Usage

```bash
cloudx bgp-peer create isp1 --router router1 --peer-ip 172.17.0.200 \
    --local-as 65000 --remote-as 65100 --auth-password secret123 \
    --advertised-prefix 10.0.0.0/24
cloudx bgp-peer list          # status, status_reason, outer_ip
cloudx bgp-peer show isp1     # including learned_routes
cloudx bgp-peer sync-config isp1   # bird_config (password masked)
```

`sync-config` returns `bird_config`; the `frr_config` key is kept for compatibility and holds the same value.

## Troubleshooting

```bash
sudo journalctl -u cloudx-bgp-worker -f
sudo ip netns exec qbgp-<short> birdc -s /var/lib/cloudx/bgp/<speaker_id>/bird.ctl show protocols
sudo ip netns exec qbgp-<short> birdc -s /var/lib/cloudx/bgp/<speaker_id>/bird.ctl show route
```

Installation: `scripts/setup-compute-node.sh` installs `bird2`, disables the distro's `bird` unit, and writes `/etc/cloudx/bgp-worker/bgp-worker.conf` (`node_name`). Enable it with `systemctl enable --now cloudx-bgp-worker` on compute and storage nodes.

## Limitations

- IPv4 eBGP only; the peer must be on the same L2 as the external network (no multihop).
- One host per speaker, no failover.
- MD5 passwords are stored in plaintext.
- No BFD, IPv6, custom route-maps or graceful restart; export is only `advertised_prefixes`.
- Default routes, and prefixes from the peer that would override a router subnet, are ignored.
- Inbound traffic to private prefixes that the router SNATs depends on the reply path being symmetric.
- The speaker's `outer_ip` uses one address from the external network pool.
