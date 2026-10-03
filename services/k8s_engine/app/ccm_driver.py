def create_ccm_loadbalancer_payload(svc_request: dict) -> dict:
    svc_name = svc_request.get("service_name", "k8s-service")
    if svc_name.startswith("k8s-"):
        svc_name = svc_name[4:]
    return {
        "name": f"ccm-{svc_name}",
        "protocol": "HTTP",
        "port": svc_request.get("port", 80),
        "target_port": svc_request.get("target_port", 30080),
        "members": svc_request.get("node_ips", []),
        "project_id": svc_request.get("project_id", "admin")
    }
