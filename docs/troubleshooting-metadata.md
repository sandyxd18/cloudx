# Troubleshooting Metadata

Check the services:

```bash
docker compose ps metadata-api metadata-proxy
curl http://localhost:8775/health
curl http://localhost:8776/health
```

Check the debug lookup from the host:

```bash
server_id="$(compute server show vm1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
port_id="$(compute server show vm1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["port_id"])')"
curl "http://localhost:8775/openstack/latest/meta_data.json?instance_id=${server_id}"
curl "http://localhost:8775/openstack/latest/network_data.json?port_id=${port_id}"
```

If the VM cannot reach `169.254.169.254`, check the host rule:

```bash
sudo scripts/setup-metadata-route.sh --dry-run
sudo iptables -t nat -S | grep 169.254.169.254
```

Make sure the private subnet's router has a metadata route, or a host path that points to metadata-proxy. The config-drive stays available as a fallback while metadata over HTTP is not yet reachable.
