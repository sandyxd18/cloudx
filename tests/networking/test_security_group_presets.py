from fastapi.testclient import TestClient


def test_security_group_presets(monkeypatch, networking):

    try:
        from shared.auth_middleware import require_auth
    except ImportError:
        from shared.auth_middleware import require_auth

    networking.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    client = TestClient(networking.app)

    # Mock OVN command
    monkeypatch.setattr(networking.ovn_cli, "ovn", lambda *args, **kwargs: "")

    # GET presets
    res_presets = client.get("/security-groups/presets")
    assert res_presets.status_code == 200
    presets = res_presets.json()["presets"]
    assert "ssh" in presets
    assert "web" in presets

    # Create security group
    res_sg = client.post("/security-groups", json={"name": "web-sg", "description": "Web SG"})
    assert res_sg.status_code == 201
    sg_id = res_sg.json()["id"]

    # Apply 'web' preset
    res_apply = client.post(f"/security-groups/{sg_id}/apply-preset", json={"preset": "web"})
    assert res_apply.status_code == 200
    assert res_apply.json()["rules_added"] == 2

    # Check security group detail rules
    res_detail = client.get(f"/security-groups/{sg_id}")
    assert res_detail.status_code == 200
    rules = res_detail.json()["security_group_rules"]
    assert len(rules) == 2
