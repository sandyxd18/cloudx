# VPNaaS IPsec site-to-site

Real site-to-site VPN: one strongSwan `charon` per router, running in the netns `qvpn-<short>` on the router's gateway chassis host, managed by `cloudx-vpn-worker`.

## Topology

```
local subnet -- router (OVN) --LRP-- LS vpn-<gw> --LSP-- [qvpn-<short>: "in" .2/30]
                                                           charon (strongSwan)
public-net -------------------- LS public --LSP-- [qvpn-<short>: "out" = outer_ip]
```

- Transit `/30` from `100.101.0.0/16`: router `.1`, netns `.2`. The router gets a static route for `peer_cidrs` via `.2`.
- `outer_ip` comes from the external network pool (not the router's gateway IP). The router must have an external gateway (otherwise 400).
- `peer_cidrs` that overlap a router subnet return 409.

## Behaviour

- Creating the first site connection on a router creates the gateway and the OVN objects; deleting the last one cleans everything up (OVN, netns, OVS port, directory, charon).
- `vpn-worker` polls `GET /internal/vpn-agent/<node_name>` every 5 seconds, writes `swanctl.conf` (mode 0600), runs `swanctl --load-all` only when the content changes, then reports the status (`ACTIVE` when the child SA is INSTALLED, `DOWN`, `ERROR` + `status_reason`).
- `admin_state_up=false` takes the connection out of the configuration.

## Creating both sides

A side's `peer_address` and `peer_id` are the other side's gateway `outer_ip`. Create the connection on side A, read its gateway's `outer_ip`, create the connection on side B with that as the peer, then recreate connection A with side B's gateway `outer_ip`.

## Limitations

- PSK only (no X.509).
- One host per gateway, no failover.
- The PSK is stored in plaintext in the DB and returned by `/vpn/site-connections/{id}/config`.
- No remote-access VPN and no BGP over IPsec.
- DPD cannot be configured: when the peer is deleted, the remaining side can stay `ACTIVE` until the SA expires.
- Creating both sides is a manual sequence (see above).

## Troubleshooting

```bash
sudo ip netns exec qvpn-<short> swanctl --list-sas --uri unix:///var/lib/cloudx/vpn/<gw_id>/vici
sudo journalctl -u cloudx-vpn-worker -f
```
