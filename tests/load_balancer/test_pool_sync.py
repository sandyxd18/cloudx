import importlib


def test_pool_sync_without_server_group(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/lb.db")
    import services.load_balancer.app.main as m
    importlib.reload(m)
    m.Base.metadata.create_all(m.engine)

    user = {"sub": "u1", "project_id": "p1", "role": "member"}

    with m.SessionLocal() as db:
        lb = m.LoadBalancer(id="lb1", name="lb1", project_id="p1", lb_type="ALB", vip_address="10.0.0.5", provisioning_status="ACTIVE")
        db.add(lb)
        ls = m.Listener(id="ls1", load_balancer_id="lb1", name="ls1", protocol="HTTP", protocol_port=80)
        db.add(ls)
        pool = m.Pool(id="pool1", load_balancer_id="lb1", listener_id="ls1", name="pool1", protocol="HTTP", lb_algorithm="ROUND_ROBIN")
        db.add(pool)
        member = m.Member(id="m1", pool_id="pool1", address="10.0.0.10", protocol_port=80)
        db.add(member)
        db.commit()

        res = m.sync_pool_members("pool1", user, db)
        assert "members" in res
        assert len(res["members"]) == 1
        assert res["members"][0]["id"] == "m1"
        assert res["members"][0]["address"] == "10.0.0.10"
