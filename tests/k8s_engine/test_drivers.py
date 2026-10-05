
def test_cloudx_csi_driver_provisioner():
    import services.k8s_engine.app.csi_driver as csi
    pvc_request = {
        "pv_name": "pvc-claim-12345",
        "capacity_bytes": 10737418240, # 10Gi
        "volume_type": "gp2",
        "project_id": "proj-1"
    }
    vol_payload = csi.create_csi_volume_payload(pvc_request)
    assert vol_payload["name"] == "pvc-claim-12345"
    assert vol_payload["size_gb"] == 10
    assert vol_payload["volume_type"] == "gp2"

def test_cloudx_ccm_service_provisioner():
    import services.k8s_engine.app.ccm_driver as ccm
    k8s_svc = {
        "service_name": "k8s-ingress-nginx",
        "namespace": "ingress-nginx",
        "port": 80,
        "target_port": 30080,
        "node_ips": ["10.0.0.15", "10.0.0.16"],
        "project_id": "proj-1"
    }
    lb_payload = ccm.create_ccm_loadbalancer_payload(k8s_svc)
    assert lb_payload["name"] == "ccm-ingress-nginx"
    assert lb_payload["protocol"] == "HTTP"
    assert len(lb_payload["members"]) == 2
