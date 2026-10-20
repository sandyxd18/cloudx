import importlib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from shared.auth_middleware import require_auth

@pytest.fixture
def mq(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'mq.sqlite'}")
    import services.messaging.app.main as m
    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    who = {"project_id": "p1", "role": "member"}
    m.app.dependency_overrides[require_auth] = lambda: who
    c = TestClient(m.app)
    c.who, c.m = who, m
    return c

def post(c, *bodies, q="jobs", ttl=3600):
    return c.post(f"/queues/{q}/messages", json={"messages": [{"body": b, "ttl": ttl} for b in bodies]})

def test_flow_claim_ack_and_redelivery(mq):
    assert mq.put("/queues/jobs").status_code == 201
    assert mq.put("/queues/jobs").status_code == 409
    ids = post(mq, {"n": 1}, "two", 3).json()["ids"]
    assert mq.get("/queues/jobs/stats").json() == {"total": 3, "free": 3, "claimed": 0}
    assert len(mq.get("/queues/jobs/messages").json()["items"]) == 3          # peek does not claim
    r = mq.post("/queues/jobs/claims?limit=2").json()
    assert [i["body"] for i in r["items"]] == [{"n": 1}, "two"]               # FIFO
    assert mq.post("/queues/jobs/claims?limit=5").json()["items"][0]["body"] == 3   # claimed ones are skipped
    mid = r["items"][0]["id"]
    assert mq.delete(f"/queues/jobs/messages/{mid}").status_code == 409        # needs the claim_id
    assert mq.delete(f"/queues/jobs/messages/{mid}?claim_id=wrong").status_code == 409
    assert mq.delete(f"/queues/jobs/messages/{mid}?claim_id={r['claim_id']}").status_code == 204
    assert mq.delete(f"/queues/jobs/claims/{r['claim_id']}").status_code == 204   # releases the other one
    again = mq.post("/queues/jobs/claims").json()["items"]
    assert [i["body"] for i in again] == ["two"] and again[0]["deliveries"] == 2
    assert ids[0] == mid

def test_expired_claim_comes_back_and_expired_message_is_hidden(mq):
    mq.put("/queues/jobs")
    post(mq, "a", "b")
    mq.post("/queues/jobs/claims?limit=1")
    with mq.m.SessionLocal() as db:
        rows = list(db.scalars(mq.m.select(mq.m.Message).order_by(mq.m.Message.id)))
        rows[0].claimed_until = mq.m.now() - timedelta(seconds=1)
        rows[1].expires_at = mq.m.now() - timedelta(seconds=1)
        db.commit()
    got = mq.post("/queues/jobs/claims").json()["items"]
    assert [g["body"] for g in got] == ["a"] and got[0]["deliveries"] == 2
    assert mq.get("/queues/jobs/stats").json()["total"] == 1
    post(mq, "c")                                                              # a post purges the expired row
    with mq.m.SessionLocal() as db:
        assert db.scalar(mq.m.select(mq.m.func.count()).select_from(mq.m.Message)) == 2

def test_validation_and_limits(mq):
    assert mq.put("/queues/bad name").status_code in (404, 422)
    assert mq.put("/queues/x%0A").status_code in (404, 422)
    mq.put("/queues/jobs")
    assert post(mq, "x", ttl=5).status_code == 422
    assert post(mq, *range(11)).status_code == 422
    assert post(mq, "x" * 70000).status_code == 413
    assert mq.post("/queues/jobs/claims?ttl=1").status_code == 422
    assert post(mq, "x", q="nope").status_code == 404
    mq.m.MAX_ACTIVE = 2
    assert post(mq, 1, 2).status_code == 201
    assert post(mq, 3).status_code == 429

def test_project_isolation_and_delete(mq):
    mq.put("/queues/jobs")
    post(mq, "a")
    mq.who["project_id"] = "p2"
    assert mq.get("/queues/jobs/stats").status_code == 404
    assert mq.get("/queues").json()["items"] == []
    mq.who["project_id"] = "p1"
    assert mq.delete("/queues/jobs").status_code == 204
    assert mq.get("/queues/jobs/stats").status_code == 404
