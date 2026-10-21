from . import clients
from .config import image_api_url, networking_api_url, log
from .models import Server


def obj_to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


# Image id → name cache: server_to_dict enriches every server response with the
# image name, and GET /servers calls it per row — one image-api HTTP call per
# row per request is wasteful, and image names are effectively immutable.
# ponytail: unbounded dict cache is fine for a lab; move to TTL/memo if the
# image catalog ever gets large or churns.
_image_name_cache: dict[str, str] = {}


def server_to_dict(server: Server) -> dict:
    data = obj_to_dict(server)
    data["metadata_url_debug"] = f"http://localhost:8775/openstack/latest/meta_data.json?instance_id={server.id}"

    # ── Enrich image name from image-api (cached, best-effort) ─────────────────
    if server.image_id:
        cached = _image_name_cache.get(server.image_id)
        if cached:
            data["image_name"] = cached
        else:
            try:
                image = clients.api_get(f"{image_api_url()}/images/{server.image_id}")
                name = image.get("name")
                if name:
                    _image_name_cache[server.image_id] = name
                    data["image_name"] = name
            except Exception as exc:
                log.debug("image name lookup failed: %s", exc)

    # ── Enrich from networking-api: security groups + floating IP ──────────────
    # security_groups: read the port's security_group_ids, map to names
    if server.port_id:
        try:
            port = clients.api_get(f"{networking_api_url()}/ports/{server.port_id}")
            sg_ids = port.get("security_group_ids") or []
            if sg_ids:
                sg_names = []
                for sg_id in sg_ids:
                    try:
                        sg = clients.api_get(f"{networking_api_url()}/security-groups/{sg_id}")
                        sg_names.append(sg.get("name", sg_id))
                    except Exception as exc:
                        log.debug("security group name lookup failed: %s", exc)
                        sg_names.append(sg_id)
                data["security_groups"] = sg_names
            # floating_ip: query for a floating IP bound to this port
            fips = clients.api_get(f"{networking_api_url()}/floating-ips")
            fip_items = fips.get("items", [])
            for fip in fip_items:
                if fip.get("port_id") == server.port_id or fip.get("fixed_ip_address") == server.ip_address:
                    data["floating_ip"] = fip.get("floating_ip_address") or fip.get("floating_ip")
                    break
        except Exception as exc:
            log.debug("floating IP lookup failed: %s", exc)
    return data
