import importlib

from fastapi.testclient import TestClient


def test_load_balancer_ssl_certificate(monkeypatch, tmp_path):
    db_path = tmp_path / "lb_ssl.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.load_balancer.app.main as lb_main

    lb_main = importlib.reload(lb_main)
    lb_main.Base.metadata.create_all(bind=lb_main.engine)

    from shared.auth_middleware import require_auth
    lb_main.app.dependency_overrides[require_auth] = lambda: {"project_id": "proj-1", "role": "admin"}
    client = TestClient(lb_main.app)

    # Seed LB
    with lb_main.SessionLocal() as db:
        lb = lb_main.LoadBalancer(id="lb-ssl-1", name="lb-https", project_id="proj-1", lb_type="ALB", vip_address="10.0.0.100")
        db.add(lb)
        db.commit()

    # Upload certificate
    res_cert = client.post(
        "/load-balancers/lb-ssl-1/certificates",
        json={
            "name": "my-ssl-cert",
            "certificate_pem": "-----BEGIN CERTIFICATE-----\nFAKE_CERT\n-----END CERTIFICATE-----",
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nFAKE_KEY\n-----END PRIVATE KEY-----",
        },
    )
    assert res_cert.status_code == 201
    cert_id = res_cert.json()["id"]

    # Create HTTPS listener with certificate_id
    res_ls = client.post(
        "/load-balancers/lb-ssl-1/listeners",
        json={"name": "https-443", "protocol": "HTTPS", "protocol_port": 443, "certificate_id": cert_id},
    )
    assert res_ls.status_code == 201
    data = res_ls.json()
    assert data["certificate_id"] == cert_id
    assert data["ssl_cert_path"] is not None
    assert "FAKE_KEY" not in res_ls.text  # the key never appears in the public API

    # lb-worker gets the PEM through the internal endpoint and writes it at ssl_cert_path itself
    from shared.auth_middleware import require_internal
    lb_main.app.dependency_overrides[require_internal] = lambda: {}
    with lb_main.SessionLocal() as db:
        db.query(lb_main.LoadBalancer).filter_by(id="lb-ssl-1").update({"host": "n1"})
        db.commit()
    got = client.get("/internal/lb-agent/n1").json()["load_balancers"][0]["listeners"][0]
    assert got["ssl_cert_path"] == data["ssl_cert_path"] and "FAKE_KEY" in got["ssl_pem"]
    assert client.delete("/load-balancers/lb-ssl-1").status_code == 200  # cert rows must not block the delete
