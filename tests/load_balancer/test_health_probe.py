import importlib


def test_load_balancer_health_probe_logic(monkeypatch, tmp_path):
    db_path = tmp_path / "lb_health.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import services.load_balancer.app.main as lb_main

    lb_main = importlib.reload(lb_main)
    lb_main.Base.metadata.create_all(bind=lb_main.engine)

    with lb_main.SessionLocal() as db:
        lb = lb_main.LoadBalancer(id="lb-1", name="lb-test", project_id="proj-1", lb_type="ALB")
        pool = lb_main.Pool(id="pool-1", load_balancer_id="lb-1", listener_id="ls-1", name="p1", protocol="HTTP")
        member = lb_main.Member(id="m-1", pool_id="pool-1", address="127.0.0.1", protocol_port=9999, operating_status="ONLINE")
        hm = lb_main.HealthMonitor(id="hm-1", pool_id="pool-1", type="TCP", delay=5, timeout=1, max_retries=2)
        db.add_all([lb, pool, member, hm])
        db.commit()

    # Stub driver apply state
    applied = []
    monkeypatch.setattr(lb_main, "_apply_lb_state", lambda db, lb: applied.append(lb.id))

    # Run health monitor check (member 127.0.0.1:9999 should fail TCP connection)
    lb_main._run_health_monitors()
    lb_main._run_health_monitors()

    with lb_main.SessionLocal() as db:
        m = db.get(lb_main.Member, "m-1")
        assert m.operating_status == "OFFLINE"
    assert len(applied) >= 1
