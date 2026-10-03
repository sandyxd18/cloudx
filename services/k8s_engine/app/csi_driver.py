def create_csi_volume_payload(pvc_request: dict) -> dict:
    return {
        "name": pvc_request.get("pv_name"),
        "size_gb": pvc_request.get("capacity_bytes", 1073741824) // (1024 ** 3),
        "volume_type": pvc_request.get("volume_type", "gp2"),
        "project_id": pvc_request.get("project_id", "admin")
    }
