
import pytest


def test_compute_rate_first_sample_has_no_rate_yet():
    """First sample for a server can't compute a delta — cpu%/rates default to 0."""
    pytest.importorskip("fastapi")
    from services.compute_worker.app import main

    current = {
        "timestamp": 1000.0, "cpu_time_ns": 4_000_000_000, "nr_vcpus": 2,
        "mem_total_kb": 1048576, "mem_used_kb": 524288,
        "net_rx_bytes": 1000, "net_tx_bytes": 2000,
        "disk_read_bytes": 4096000, "disk_write_bytes": 2048000,
        "disk_read_req": 100, "disk_write_req": 50,
    }
    sample = main._compute_rate(current, None)

    assert sample["cpu_percent"] == 0.0
    assert sample["net_rx_bps"] == 0.0
    assert sample["mem_used_mb"] == 512.0
    assert sample["mem_total_mb"] == 1024.0
    assert sample["mem_percent"] == 50.0

def test_compute_rate_computes_cpu_percent_and_throughput_from_delta():
    """Second sample: CPU% derived from cpu_time_ns delta / (elapsed * nr_vcpus), rates from byte deltas."""
    pytest.importorskip("fastapi")
    from services.compute_worker.app import main

    previous = {
        "timestamp": 1000.0, "cpu_time_ns": 4_000_000_000, "nr_vcpus": 2,
        "mem_total_kb": 1048576, "mem_used_kb": 524288,
        "net_rx_bytes": 1000, "net_tx_bytes": 2000,
        "disk_read_bytes": 4096000, "disk_write_bytes": 2048000,
        "disk_read_req": 100, "disk_write_req": 50,
    }
    # 10s later: 1 full vCPU-second of CPU time consumed out of 2 vCPUs * 10s = 20 vCPU-seconds -> 5%
    current = {
        **previous,
        "timestamp": 1010.0,
        "cpu_time_ns": 4_000_000_000 + 1_000_000_000,
        "net_rx_bytes": 1000 + 10_000,   # +10KB over 10s -> 1000 B/s
        "net_tx_bytes": 2000 + 20_000,
        "disk_read_bytes": 4096000 + 102_400,
        "disk_write_bytes": 2048000 + 51_200,
        "disk_read_req": 100 + 20,        # +20 req over 10s -> 2 IOPS
        "disk_write_req": 50 + 10,
    }
    sample = main._compute_rate(current, previous)

    assert sample["cpu_percent"] == 5.0
    assert sample["net_rx_bps"] == 1000.0
    assert sample["net_tx_bps"] == 2000.0
    assert sample["disk_read_bps"] == 10240.0
    assert sample["disk_write_bps"] == 5120.0
    assert sample["disk_read_iops"] == 2.0
    assert sample["disk_write_iops"] == 1.0

def test_compute_rate_clamps_cpu_percent_to_100(monkeypatch):
    """A pathological delta (e.g. clock skew) never reports above 100% CPU."""
    pytest.importorskip("fastapi")
    from services.compute_worker.app import main

    previous = {"timestamp": 1000.0, "cpu_time_ns": 0, "nr_vcpus": 1}
    current = {"timestamp": 1001.0, "cpu_time_ns": 10_000_000_000, "nr_vcpus": 1,
               "mem_total_kb": None, "mem_used_kb": None}

    sample = main._compute_rate(current, previous)

    assert sample["cpu_percent"] == 100.0

def test_handle_get_metrics_returns_filtered_history(monkeypatch):
    """RPC handler filters the in-memory ring buffer by since_epoch and reports the sample interval."""
    pytest.importorskip("fastapi")
    from services.compute_worker.app import main

    monkeypatch.setattr(main, "_metrics_history", {
        "srv-1": [
            {"timestamp": 100.0, "cpu_percent": 1.0},
            {"timestamp": 200.0, "cpu_percent": 2.0},
        ]
    })

    result = main.handle_get_metrics({"server_id": "srv-1", "since_epoch": 150.0})

    assert result["server_id"] == "srv-1"
    assert result["samples"] == [{"timestamp": 200.0, "cpu_percent": 2.0}]
    assert result["sample_interval"] == main.METRICS_SAMPLE_INTERVAL

def test_handle_get_metrics_requires_server_id():
    pytest.importorskip("fastapi")
    from services.compute_worker.app import main

    assert main.handle_get_metrics({}) == {"error": "server_id required"}
