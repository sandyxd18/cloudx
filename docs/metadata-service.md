# Metadata Service

`metadata-api` exposes Nova-like metadata on port `8775`.

Debug from the host:

```bash
curl "http://localhost:8775/openstack/latest/meta_data.json?instance_id=<server-id>"
curl "http://localhost:8775/latest/meta-data/instance-id?instance_id=<server-id>"
```

Supported lookup inputs:

- query `instance_id`
- header `X-Instance-ID`
- header `X-Forwarded-For`
- header `X-Neutron-Port-ID`

The initial single-node milestone does not fully proxy VM traffic to `169.254.169.254` yet. Config-drive remains enabled, and direct host/container debug access is supported. The `169.254.169.254` path is experimental until a metadata proxy or OVN localport redirect is added.
