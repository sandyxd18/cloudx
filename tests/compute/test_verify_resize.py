"""A resize waits for the user: the worker leaves the server in VERIFY_RESIZE, and confirmResize or revertResize ends it.
Before, the server went straight to ACTIVE with the rollback snapshot still in the record, so a second resize quietly
overwrote the size to go back to."""
import pytest
from tests.compute import test_catalog_failures

api, _server, _status = test_catalog_failures.api, test_catalog_failures._server, test_catalog_failures._status


def _add_big(svc):
    with svc.SessionLocal() as db:
        db.add(svc.Flavor(name="big", vcpus=4, ram_mb=2048, disk_gb=1))
        db.commit()


def _payloads(svc, monkeypatch):
    seen = []
    monkeypatch.setattr(svc.clients, "cast_to_worker", lambda method, payload: seen.append((method, payload)))
    return seen


def _resized(svc, status):
    """A server whose resize to `big` is done: the record holds the size to go back to."""
    _server(svc, status, flavor_name="big", vcpus=4, ram_mb=2048, original_flavor="small", original_vcpus=1, original_ram_mb=512)


@pytest.mark.parametrize("status", ["ACTIVE", "SHUTOFF"])
def test_a_server_that_runs_or_is_stopped_can_be_resized(api, status):
    svc, client, casts, _ = api
    _add_big(svc)
    _server(svc, status)

    assert client.post("/servers/srv1/resize", json={"flavor": "big"}).status_code == 200
    assert casts == ["resize"]


@pytest.mark.parametrize("status", ["BUILD", "RESIZE", "ERROR", "SHELVED_OFFLOADED", "REBUILD"])
def test_a_server_in_any_other_state_cannot_be_resized(api, status):
    svc, client, casts, _ = api
    _add_big(svc)
    _server(svc, status)

    r = client.post("/servers/srv1/resize", json={"flavor": "big"})

    assert r.status_code == 409 and status in r.json()["detail"]
    assert casts == [] and _status(svc) == status


def test_a_second_resize_must_wait_for_the_first_to_be_confirmed_or_reverted(api):
    svc, client, casts, _ = api
    _add_big(svc)
    _resized(svc, "VERIFY_RESIZE")

    r = client.post("/servers/srv1/resize", json={"flavor": "small"})

    assert r.status_code == 409 and "confirm" in r.json()["detail"] and "revert" in r.json()["detail"]
    assert casts == []


def test_confirm_ends_the_wait_and_forgets_the_old_size(api):
    svc, client, casts, _ = api
    _resized(svc, "VERIFY_RESIZE")

    r = client.post("/servers/srv1/confirmResize")

    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
    with svc.SessionLocal() as db:
        s = db.get(svc.Server, "srv1")
        assert (s.original_flavor, s.original_vcpus, s.original_ram_mb) == (None, None, None)
    assert casts == [], "the guest already runs the new size; confirming only closes the record"


def test_confirm_also_closes_a_resize_that_an_older_version_left_unconfirmed(api):
    svc, client, _, _ = api
    _resized(svc, "ACTIVE")

    assert client.post("/servers/srv1/confirmResize").status_code == 200


@pytest.mark.parametrize("status", ["RESIZE", "BUILD", "SHUTOFF"])
def test_confirm_refuses_a_server_that_is_not_waiting(api, status):
    svc, client, _, _ = api
    _resized(svc, status)

    r = client.post("/servers/srv1/confirmResize")

    assert r.status_code == 409 and "VERIFY_RESIZE" in r.json()["detail"]


def test_confirm_of_a_server_that_was_never_resized_is_409(api):
    svc, client, _, _ = api
    _server(svc, "ACTIVE")

    assert client.post("/servers/srv1/confirmResize").status_code == 409


def test_the_resize_cast_is_not_a_revert_and_the_revert_cast_says_so(api, monkeypatch):
    svc, client, _, _ = api
    _add_big(svc)
    sent = _payloads(svc, monkeypatch)
    _server(svc, "ACTIVE", flavor_name="small", vcpus=1, ram_mb=512)

    client.post("/servers/srv1/resize", json={"flavor": "big"})
    with svc.SessionLocal() as db:
        db.get(svc.Server, "srv1").status = "VERIFY_RESIZE"
        db.commit()
    client.post("/servers/srv1/revertResize")

    assert [m for m, _ in sent] == ["resize", "resize"]
    assert not sent[0][1].get("_revert") and sent[1][1]["_revert"] is True
    assert (sent[1][1]["new_vcpus"], sent[1][1]["new_ram_mb"]) == (1, 512)


@pytest.mark.parametrize("status", ["VERIFY_RESIZE", "ACTIVE", "ERROR"])
def test_revert_works_from_the_wait_from_an_old_unconfirmed_resize_and_from_a_failed_resize(api, status):
    svc, client, casts, _ = api
    _resized(svc, status)

    r = client.post("/servers/srv1/revertResize")

    assert r.status_code == 200
    assert casts == ["resize"] and _status(svc) == "RESIZE"


@pytest.mark.parametrize("status", ["RESIZE", "BUILD"])
def test_revert_refuses_while_a_resize_is_still_running(api, status):
    svc, client, casts, _ = api
    _resized(svc, status)

    assert client.post("/servers/srv1/revertResize").status_code == 409
    assert casts == []


# ── a stopped server stays stopped ─────────────────────────────────────────────
# The worker sets ACTIVE after a resize and confirmResize set ACTIVE too. For a server that was SHUTOFF that is wrong:
# the power-state loop sees a stopped domain under an ACTIVE record and its auto-recovery starts it as if it had crashed.

def _original_status(svc):
    with svc.SessionLocal() as db:
        return db.get(svc.Server, "srv1").original_status


def test_a_resize_remembers_whether_the_server_was_running(api):
    svc, client, _, _ = api
    _add_big(svc)
    _server(svc, "SHUTOFF")

    client.post("/servers/srv1/resize", json={"flavor": "big"})

    assert _original_status(svc) == "SHUTOFF"


def test_confirm_puts_a_stopped_server_back_to_shutoff(api):
    svc, client, _, _ = api
    _resized(svc, "VERIFY_RESIZE")
    with svc.SessionLocal() as db:
        db.get(svc.Server, "srv1").original_status = "SHUTOFF"
        db.commit()

    r = client.post("/servers/srv1/confirmResize")

    assert r.json()["status"] == "SHUTOFF" and _original_status(svc) is None


def test_revert_tells_the_worker_which_status_to_end_in(api, monkeypatch):
    svc, client, _, _ = api
    sent = _payloads(svc, monkeypatch)
    _resized(svc, "VERIFY_RESIZE")
    with svc.SessionLocal() as db:
        db.get(svc.Server, "srv1").original_status = "SHUTOFF"
        db.commit()

    client.post("/servers/srv1/revertResize")

    assert sent[0][1]["_target_status"] == "SHUTOFF" and _original_status(svc) is None


def test_a_record_without_the_remembered_status_means_active(api, monkeypatch):
    svc, client, _, _ = api
    sent = _payloads(svc, monkeypatch)
    _resized(svc, "VERIFY_RESIZE")

    assert client.post("/servers/srv1/confirmResize").json()["status"] == "ACTIVE"
    _resized_again = _resized  # a second server is not needed: revert is checked on a fresh record below
    with svc.SessionLocal() as db:
        db.query(svc.Server).delete()
        db.commit()
    _resized_again(svc, "VERIFY_RESIZE")
    client.post("/servers/srv1/revertResize")
    assert sent[0][1]["_target_status"] == "ACTIVE"
