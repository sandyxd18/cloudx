"""Deleting a server that is still BUILD must still destroy its domain.

The API only learns libvirt_domain_name when the build reports back, so a delete during BUILD arrives without one. The
domain is named after the server (domain_name(name, id)), so the worker can work it out; skipping it left the VM running
on the node with its root volume open, while the API said "deleted"."""
import pytest

from services.compute_worker.app import libvirt_driver, network_manager, worker

SERVER_ID = "1d1d22be-859d-4cb7-9dcb-329b575a234d"


@pytest.fixture
def deleted(monkeypatch, tmp_path):
    calls = {"domains": [], "taps": []}
    monkeypatch.setattr(worker.libvirt_driver, "delete", lambda name: calls["domains"].append(name))
    monkeypatch.setattr(worker.network_manager, "delete_tap", lambda name: calls["taps"].append(name))
    monkeypatch.setattr(worker, "INSTANCE_DIR", tmp_path)
    return calls


def test_the_domain_name_is_worked_out_when_the_build_has_not_reported_one(deleted):
    payload = {"id": SERVER_ID, "name": "crash-test", "libvirt_domain_name": None}

    result = worker.delete_server(SERVER_ID, payload)

    assert deleted["domains"] == [libvirt_driver.domain_name("crash-test", SERVER_ID)]
    assert deleted["domains"] == ["mini-os-crash-test-1d1d22be"]
    assert result == {"status": "deleted", "id": SERVER_ID}


def test_the_stored_domain_name_wins_over_a_derived_one(deleted):
    payload = {"id": SERVER_ID, "name": "crash-test", "libvirt_domain_name": "mini-os-renamed-1d1d22be"}

    worker.delete_server(SERVER_ID, payload)

    assert deleted["domains"] == ["mini-os-renamed-1d1d22be"]


def test_the_original_name_is_used_when_the_payload_has_no_name(deleted):
    worker.delete_server(SERVER_ID, {"id": SERVER_ID, "_original_name": "crash-test"})

    assert deleted["domains"] == ["mini-os-crash-test-1d1d22be"]


def test_without_any_name_nothing_is_guessed_and_the_rest_of_the_cleanup_still_runs(deleted):
    worker.delete_server(SERVER_ID, {"id": SERVER_ID})

    assert deleted["domains"] == []
    assert deleted["taps"] == [network_manager.tap_name(SERVER_ID)]
