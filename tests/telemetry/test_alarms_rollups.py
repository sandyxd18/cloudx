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


def test_eval_periods_statistic_history_and_rollup_merge(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t2.db")
    import services.telemetry.app.main as m
    importlib.reload(m)
    m.Base.metadata.create_all(m.engine)

    now = datetime.now(timezone.utc)
    old_bucket = (now - timedelta(days=8)).replace(minute=0, second=0, microsecond=0)
    old_sample_ts = (now - timedelta(days=8)).replace(minute=10, second=0, microsecond=0)

    with m.SessionLocal() as db:
        # Pre-seed existing rollup
        db.add(m.TelemetryRollup(instance_id="i1", project_id="p1", metric_type="cpu_percent",
                                 bucket=old_bucket, avg_value=20.0, min_value=15.0, max_value=25.0, samples=2))
        # Add new raw metric samples for same bucket
        db.add_all([
            m.TelemetryMetric(instance_id="i1", project_id="p1", metric_type="cpu_percent", value=40.0, timestamp=old_sample_ts),
            m.TelemetryMetric(instance_id="i1", project_id="p1", metric_type="cpu_percent", value=60.0, timestamp=old_sample_ts),
        ])
        db.commit()

        m.rollup_and_prune(db)
        r_list = db.query(m.TelemetryRollup).all()
        assert len(r_list) == 1
        r = r_list[0]
        # Existing (20.0, count=2) + New batch (50.0, count=2) -> Combined avg (20*2 + 50*2)/4 = 35.0, min=15, max=60, samples=4
        assert (r.avg_value, r.min_value, r.max_value, r.samples) == (35.0, 15.0, 60.0, 4)

        # Test evaluation_periods and statistic
        a = m.TelemetryAlarm(project_id="p1", name="ep_test", metric_type="cpu_percent", comparison=">",
                             threshold=50.0, period=300, evaluation_periods=2, statistic="max")
        db.add(a)
        db.commit()
        alarm_id = a.id

        # 1st evaluation with breaching max (sample 90.0) -> breach_count becomes 1, but evaluation_periods=2 so stays insufficient_data
        db.add(m.TelemetryMetric(instance_id="i1", project_id="p1", metric_type="cpu_percent", value=90.0, timestamp=now))
        db.commit()

        changed1 = m.evaluate_alarms(db, now)
        a_db = db.get(m.TelemetryAlarm, alarm_id)
        assert a_db.breach_count == 1
        assert a_db.state == "insufficient_data"  # not reached evaluation_periods yet

        # 2nd evaluation with breaching sample -> breach_count becomes 2 >= 2 -> state becomes "alarm"
        now2 = now + timedelta(seconds=10)
        db.add(m.TelemetryMetric(instance_id="i1", project_id="p1", metric_type="cpu_percent", value=90.0, timestamp=now2))
        db.commit()

        changed2 = m.evaluate_alarms(db, now2)
        a_db = db.get(m.TelemetryAlarm, alarm_id)
        assert changed2 == 1
        assert a_db.breach_count == 2
        assert a_db.state == "alarm"

        # Check history recorded state transition
        hist = db.query(m.TelemetryAlarmHistory).filter_by(alarm_id=alarm_id).all()
        assert len(hist) == 1
        assert hist[0].old_state == "insufficient_data"
        assert hist[0].new_state == "alarm"
        assert hist[0].value == 90.0

        # Non-breaching evaluation (300s later so 90.0 samples age out of period window) -> breach_count resets to 0 and state returns to "ok"
        now3 = now2 + timedelta(seconds=350)
        db.add(m.TelemetryMetric(instance_id="i1", project_id="p1", metric_type="cpu_percent", value=10.0, timestamp=now3))
        db.commit()

        changed3 = m.evaluate_alarms(db, now3)
        a_db = db.get(m.TelemetryAlarm, alarm_id)
        assert changed3 == 1
        assert a_db.breach_count == 0
        assert a_db.state == "ok"

        # History should now have 2 items
        hist = db.query(m.TelemetryAlarmHistory).filter_by(alarm_id=alarm_id).all()
        assert len(hist) == 2

        # Deleting alarm deletes its history
        db.delete(a_db)
        db.commit()
        assert db.query(m.TelemetryAlarmHistory).filter_by(alarm_id=alarm_id).count() == 0