from datetime import datetime, timedelta
import importlib
from unittest.mock import MagicMock, patch


def test_lb_agent_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/lb.db")
    import services.load_balancer.app.main as m
    importlib.reload(m)
    m.Base.metadata.create_all(m.engine)

    with m.SessionLocal() as db:
        m.lb_agent_state("node-1", db)
        agent = db.query(m.LBAgentHost).filter_by(host="node-1").first()
        assert agent is not None
        assert agent.host == "node-1"
        first_seen = agent.last_seen

        # Subsequent tick updates last_seen
        m.lb_agent_state("node-1", db)
        agent2 = db.query(m.LBAgentHost).filter_by(host="node-1").first()
        assert agent2.last_seen >= first_seen


def test_pick_host_among_alive_hosts(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/lb.db")
    import services.load_balancer.app.main as m
    importlib.reload(m)
    m.Base.metadata.create_all(m.engine)

    with m.SessionLocal() as db:
        # node-1 stale (60s ago), node-2 active (5s ago)
        stale_time = datetime.utcnow() - timedelta(seconds=60)
        recent_time = datetime.utcnow() - timedelta(seconds=5)

        db.add(m.LBAgentHost(host="node-1", last_seen=stale_time))
        db.add(m.LBAgentHost(host="node-2", last_seen=recent_time))
        db.commit()

        # Mock compute os-services returning node-1 and node-2 as enabled
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "services": [
                {"host": "node-1", "status": "enabled", "forced_down": False},
                {"host": "node-2", "status": "enabled", "forced_down": False},
            ]
        }
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            picked = m._pick_host(db)
            assert picked == "node-2"


def test_stale_host_triggers_failover(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/lb.db")
    import services.load_balancer.app.main as m
    importlib.reload(m)
    m.Base.metadata.create_all(m.engine)

    events = []

    class DummyRPC:
        def publish_event(self, name, payload):
            events.append((name, payload))

    with m.SessionLocal() as db:
        lb = m.LoadBalancer(
            id="alb-12345678",
            name="test-alb",
            project_id="p1",
            lb_type="ALB",
            vip_address="10.0.0.5",
            host="node-1",
            provisioning_status="ACTIVE",
            operating_status="ONLINE",
        )
        db.add(lb)

        # node-1 heartbeat is 60s ago (stale), node-2 heartbeat is 2s ago (alive)
        stale_time = datetime.utcnow() - timedelta(seconds=60)
        recent_time = datetime.utcnow() - timedelta(seconds=2)
        db.add(m.LBAgentHost(host="node-1", last_seen=stale_time))
        db.add(m.LBAgentHost(host="node-2", last_seen=recent_time))
        db.commit()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "services": [
                {"host": "node-1", "status": "enabled", "forced_down": False},
                {"host": "node-2", "status": "enabled", "forced_down": False},
            ]
        }
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp), patch("shared.rpc.RPCClient", return_value=DummyRPC()):
            m._sync_alb_failover(db)

        updated_lb = db.query(m.LoadBalancer).filter_by(id="alb-12345678").first()
        assert updated_lb.host == "node-2"
        assert updated_lb.provisioning_status == "ACTIVE"
        assert len(events) == 1
        assert events[0][0] == "load_balancer.failover"
        assert events[0][1]["old_host"] == "node-1"
        assert events[0][1]["new_host"] == "node-2"


def test_no_alive_host_leaves_error(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/lb.db")
    import services.load_balancer.app.main as m
    importlib.reload(m)
    m.Base.metadata.create_all(m.engine)

    with m.SessionLocal() as db:
        lb = m.LoadBalancer(
            id="alb-99999999",
            name="test-alb-2",
            project_id="p1",
            lb_type="ALB",
            vip_address="10.0.0.6",
            host="node-1",
            provisioning_status="ACTIVE",
            operating_status="ONLINE",
        )
        db.add(lb)

        # node-1 is stale, no other host exists
        stale_time = datetime.utcnow() - timedelta(seconds=60)
        db.add(m.LBAgentHost(host="node-1", last_seen=stale_time))
        db.add(m.LBAgentHost(host="node-2", last_seen=datetime.utcnow()))  # alive but not an enabled compute host
        db.commit()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "services": [
                {"host": "node-1", "status": "enabled", "forced_down": False},
            ]
        }
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            m._sync_alb_failover(db)

        updated_lb = db.query(m.LoadBalancer).filter_by(id="alb-99999999").first()
        assert updated_lb.host == "node-1"
        assert updated_lb.operating_status == "ERROR"


def test_worker_teardown_unowned_lbs(monkeypatch, tmp_path):
    monkeypatch.setenv("NODE_NAME", "node-1")
    monkeypatch.setenv("LB_STATE_DIR", str(tmp_path / "lb_state"))

    import services.lb_worker.app.main as w
    importlib.reload(w)

    lb_dir = tmp_path / "lb_state" / "alb-stale123"
    lb_dir.mkdir(parents=True)
    (lb_dir / "haproxy.cfg").write_text("global\n")

    w._applied["alb-stale123"] = "hash123"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"load_balancers": []}  # agent returns empty list (LB moved away)
    mock_resp.raise_for_status.return_value = None

    commands = []

    def fake_sh(*cmd, ok=False):
        commands.append(cmd)
        if cmd[:3] == ("ip", "netns", "list"):
            return "qlb-alb-stal (id: 1)\n"
        return ""

    with patch("requests.get", return_value=mock_resp), patch("services.lb_worker.app.main.sh", side_effect=fake_sh):
        w.tick()

    assert not lb_dir.exists()
    assert "alb-stale123" not in w._applied

    # Verify OVS port and netns deletion commands were executed
    ovs_dels = [c for c in commands if "ovs-vsctl" in c and "del-port" in c]
    netns_dels = [c for c in commands if "netns" in c and "del" in c]
    assert len(ovs_dels) > 0
    assert len(netns_dels) > 0
