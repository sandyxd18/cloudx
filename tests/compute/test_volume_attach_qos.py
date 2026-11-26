"""The attach call from block-store carries the limits of the volume's type, and compute-api hands them to the worker."""
from tests.compute.test_volume_attach_internal import BODY, INTERNAL, compute  # noqa: F401  (compute is a fixture)


def test_the_limits_reach_the_worker(compute):  # noqa: F811
    _, client, casts = compute

    r = client.post("/servers/srv1/volumes/attach", json={**BODY, "max_iops": 500, "max_throughput": 20}, headers=INTERNAL)

    assert r.status_code == 200, r.text
    method, payload = casts[0]
    assert method == "attach_volume"
    assert payload["volume"]["max_iops"] == 500 and payload["volume"]["max_throughput"] == 20


def test_a_call_without_limits_carries_no_limit_keys(compute):  # noqa: F811
    _, client, casts = compute

    r = client.post("/servers/srv1/volumes/attach", json=BODY, headers=INTERNAL)

    assert r.status_code == 200, r.text
    assert "max_iops" not in casts[0][1]["volume"] and "max_throughput" not in casts[0][1]["volume"]
