
import pytest
from pydantic import ValidationError

from shared.haproxy_cfg import render

def _render(tmp_path, **over):
    lb = {"id": "lb1", "vip_address": "10.0.0.5"}
    ls = {"id": "l1", "protocol": "HTTP", "protocol_port": 80, "default_pool_id": "p1", "policies": [],
          "pools": [{"id": "p1", "lb_algorithm": "ROUND_ROBIN", "health_monitor": {"type": "HTTP", "url_path": "/h", "delay": 5, "max_retries": 3},
                     "members": [{"id": "m1", "address": "10.0.0.9", "protocol_port": 80, "weight": 1}]}]}
    ls.update(over.get("ls", {}))
    ls["pools"][0]["members"][0].update(over.get("m", {}))
    if "hm" in over:
        ls["pools"][0]["health_monitor"] = over["hm"]
    return render(lb, [ls])
def test_driver_renders_and_rejects_injection(tmp_path):
    assert "option httpchk GET /h" in _render(tmp_path)
    for bad in ({"hm": {"type": "HTTP", "url_path": "/\n  server evil 6.6.6.6:1", "delay": 5, "max_retries": 3}},
                {"ls": {"protocol": "HTTPS", "ssl_cert_path": "/x.pem\nbind *:22"}},
                {"ls": {"policies": [{"action": "REJECT", "position": 1, "rules": [{"type": "PATH", "compare_type": "EQUAL_TO", "value": "/a b"}]}]}},
                {"m": {"address": "10.0.0.9:1 check\n server evil"}}):
        with pytest.raises(ValueError):
            _render(tmp_path, **bad)


def test_schemas_reject_injection(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lb.sqlite'}")
    import importlib

    import services.load_balancer.app.main as m

    m = importlib.reload(m)
    for url in ("/x\nserver evil", "nopath", "/a b"):
        with pytest.raises(ValidationError):
            m.HealthMonitorIn(type="HTTP", url_path=url)
    m.HealthMonitorIn(type="HTTP", url_path="/health?a=1")
    with pytest.raises(ValidationError):
        m.PoolMemberIn(address="1.2.3.4\nserver evil")
    with pytest.raises(ValidationError):
        m.PoolIn(name="p\nx", protocol_port=80)
