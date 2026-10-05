# Metadata Proxy

`metadata-proxy` menerima request VM untuk path metadata dan meneruskannya ke `metadata-api`. Proxy berjalan di host network pada port `8776` agar source IP VM bisa dipakai untuk lookup.

Environment:

```env
METADATA_API_URL=http://metadata-api:8775
NETWORKING_API_URL=http://networking-api:8001
METADATA_PROXY_PORT=8776
```

Proxy lookup:

1. Ambil source IP request atau `X-Forwarded-For`.
2. Query `networking-api /ports`.
3. Cocokkan `fixed_ip` atau `ip_address`.
4. Forward ke metadata-api dengan `X-Forwarded-For`, `X-Networking-Port-ID`, dan `X-Instance-ID`.

Untuk setup host route metadata:

```bash
sudo scripts/setup-metadata-route.sh --dry-run
sudo scripts/setup-metadata-route.sh --force
```

Cleanup rule metadata:

```bash
sudo scripts/cleanup-metadata-route.sh --dry-run
sudo scripts/cleanup-metadata-route.sh --force
```

Rule hanya menarget `169.254.169.254:80` ke port metadata-proxy. Ini tetap single-node lab behavior, bukan metadata-agent CloudX penuh.
