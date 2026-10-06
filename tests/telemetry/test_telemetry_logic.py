import pytest


@pytest.fixture
def w():
    from services.telemetry_worker.app import main
    return main


def test_cpu_percent(w):
    # 1 vCPU-second used out of 2 vCPUs * 10s = 5%
    assert w.cpu_percent(0, 10**9, 10, 2) == pytest.approx(5.0)
    assert w.cpu_percent(0, 10**12, 10, 2) == 100.0  # clamp high
    assert w.cpu_percent(10**9, 0, 10, 2) == 0.0  # counter reset clamps low
    assert w.cpu_percent(0, 10**9, 0, 2) == 0.0  # no time elapsed


def test_counter_rate(w):
    assert w.counter_rate(100, 400, 10) == 30.0
    assert w.counter_rate(400, 100, 10) == 0.0  # counter reset
    assert w.counter_rate(1, 2, 0) == 0.0


def test_apply_rates_skips_first_sample(w, monkeypatch):
    w._prev.clear()
    t = iter([100.0, 110.0])
    monkeypatch.setattr(w.time, "monotonic", lambda: next(t))
    first, second = {}, {}
    w._apply_rates("i", {"cpu_ns": 0, "vcpus": 2, "net_rx_bytes": 1000}, first)
    w._apply_rates("i", {"cpu_ns": 10**9, "vcpus": 2, "net_rx_bytes": 2000}, second)
    assert first == {}
    assert second["cpu_percent"] == pytest.approx(5.0)
    assert second["net_rx_bytes"] == 100.0
