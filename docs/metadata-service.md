# Metadata Service

`metadata-api` menyediakan metadata instance gaya Compute pada port `8775`. Service ini mendukung EC2-compatible metadata dan CloudX metadata.

Endpoint utama:

```bash
curl http://localhost:8775/health
curl "http://localhost:8775/latest/meta-data/instance-id?instance_id=<server-id>"
curl "http://localhost:8775/openstack/latest/meta_data.json?instance_id=<server-id>"
curl "http://localhost:8775/openstack/latest/network_data.json?instance_id=<server-id>"
```

Lookup metadata dilakukan berurutan dari `X-Instance-ID`, query `instance_id`, `X-Networking-Port-ID`, query `port_id`, query `fixed_ip`, lalu source IP request atau `X-Forwarded-For`. Jika tidak ditemukan, API mengembalikan `404` dan tidak fallback ke instance lain.

`meta_data.json` berisi `uuid`, `name`, `hostname`, `launch_index`, `public_keys`, `meta`, dan `devices`. `network_data.json` mengambil CIDR, gateway, dan DNS dari data port/subnet networking-api.

User-data HTTP mengembalikan user-data instance jika ada. Jika kosong, metadata-api mengembalikan fallback minimal:

```yaml
#cloud-config
package_update: false
```

Config-drive NoCloud tetap dibuat saat spawn VM dan menjadi fallback yang lebih reliable untuk single-node lab.
