# Metadata Service

`metadata-api` provides Compute-style instance metadata on port `8775`. The service supports EC2-compatible metadata and CloudX metadata.

Main endpoints:

```bash
curl http://localhost:8775/health
curl "http://localhost:8775/latest/meta-data/instance-id?instance_id=<server-id>"
curl "http://localhost:8775/openstack/latest/meta_data.json?instance_id=<server-id>"
curl "http://localhost:8775/openstack/latest/network_data.json?instance_id=<server-id>"
```

Metadata lookup is done in this order: `X-Instance-ID`, query `instance_id`, `X-Networking-Port-ID`, query `port_id`, query `fixed_ip`, then the request's source IP or `X-Forwarded-For`. If nothing is found, the API returns `404` and does not fall back to another instance.

`meta_data.json` contains `uuid`, `name`, `hostname`, `launch_index`, `public_keys`, `meta`, and `devices`. `network_data.json` takes the CIDR, gateway, and DNS from the networking-api port/subnet data.

HTTP user-data returns the instance's user-data if it exists. If it is empty, metadata-api returns a minimal fallback:

```yaml
#cloud-config
package_update: false
```

The NoCloud config-drive is still created when a VM is spawned and is the more reliable fallback for the single-node lab.
