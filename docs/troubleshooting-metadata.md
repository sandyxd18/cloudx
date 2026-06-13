# Troubleshooting Metadata

Cek service:

```bash
docker compose ps metadata-api metadata-proxy
curl http://localhost:8775/health
curl http://localhost:8776/health
```

Cek debug lookup dari host:

```bash
server_id="$(compute server show vm1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
port_id="$(compute server show vm1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["port_id"])')"
curl "http://localhost:8775/openstack/latest/meta_data.json?instance_id=${server_id}"
curl "http://localhost:8775/openstack/latest/network_data.json?port_id=${port_id}"
```

Jika VM tidak bisa akses `169.254.169.254`, cek rule host:

```bash
sudo scripts/setup-metadata-route.sh --dry-run
sudo iptables -t nat -S | grep 169.254.169.254
```

Pastikan router private subnet punya route metadata atau host path yang mengarah ke metadata-proxy. Config-drive tetap tersedia sebagai fallback saat metadata HTTP belum reachable.
