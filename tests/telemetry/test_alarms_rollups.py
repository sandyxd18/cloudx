from datetime import datetime, timedelta, timezone
import importlib


def test_rollup_and_alarm(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    import services.telemetry.app.main as m
    importlib.reload(m)
    m.Base.metadata.create_all(m.engine)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=8)).replace(minute=10, second=0, microsecond=0)
    with m.SessionLocal() as db:
        db.add_all([m.TelemetryMetric(instance_id="i", project_id="p", metric_type="cpu_percent", value=v, timestamp=old)
                    for v in (10.0, 30.0)])
        db.add(m.TelemetryMetric(instance_id="i", project_id="p", metric_type="cpu_percent", value=90.0, timestamp=now))
        db.commit()
        m.rollup_and_prune(db)
        r = db.query(m.TelemetryRollup).one()
        assert (r.avg_value, r.min_value, r.max_value, r.samples) == (20.0, 10.0, 30.0, 2)
        assert db.query(m.TelemetryMetric).count() == 1  # fresh sample kept

        events = []
        monkeypatch.setattr(m, "_publish", lambda a, old_s, v: events.append((a.state, old_s)))
        db.add(m.TelemetryAlarm(project_id="p", name="hot", metric_type="cpu_percent", comparison=">", threshold=80, period=300))
        db.add(m.TelemetryAlarm(project_id="p", name="cold", metric_type="cpu_percent", comparison="<", threshold=50, period=300))
        db.add(m.TelemetryAlarm(project_id="p", name="none", metric_type="memory_used_mb", comparison=">", threshold=1, period=300))
        db.commit()
        assert m.evaluate_alarms(db, now) == 2
        states = {a.name: a.state for a in db.query(m.TelemetryAlarm)}
        assert states == {"hot": "alarm", "cold": "ok", "none": "insufficient_data"}
        assert m.evaluate_alarms(db, now) == 0  # no change, no event