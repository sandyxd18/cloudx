import os
import stat

import pytest
from pydantic import ValidationError

from services.load_balancer.app.drivers.haproxy_l7 import HAProxyL7Driver


def _render(tmp_path, **over):
    d = HAProxyL7Driver({"config_root": str(tmp_path), "pid_file": str(tmp_path / "nopid")})
    lb = {"id": "lb1", "vip_address": "10.0.0.5"}
    ls = {"protocol": "HTTP", "protocol_port": 80, "health_monitors": [{"url_path": "/h"}]}
    pool = {"id": "p1"}
    members = [{"id": "m1", "address": "10.0.0.9", "protocol_port": 80}]
    ls.update(over.get("ls", {}))
    members[0].update(over.get("m", {}))
    d.apply_pool(lb, ls, pool, members)
    return (tmp_path / "lb-lb1.cfg").read_text()


def test_driver_renders_and_rejects_injection(tmp_path):
    assert "option httpchk GET /h" in _render(tmp_path)
    for bad in ({"ls": {"health_monitors": [{"url_path": "/\n  server evil 6.6.6.6:1"}]}},
                {"ls": {"protocol": "HTTPS", "ssl_cert_path": "/x.pem\nbind *:22"}},
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


@pytest.mark.skipif(os.name == "nt", reason="posix perms")
def test_cert_file_is_private(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lb2.sqlite'}")
    import importlib

    import services.load_balancer.app.main as m

    m = importlib.reload(m)
    m.Base.metadata.create_all(bind=m.engine)
    with m.SessionLocal() as db:
        db.add(m.LBCertificate(id="c-perm", load_balancer_id="x", name="n", certificate_pem="C", private_key_pem="K"))
        db.add(m.Listener(id="l1", load_balancer_id="x", name="l", protocol="HTTPS", protocol_port=443, certificate_id="c-perm"))
        db.commit()
        out = m._listener_to_dict(db.get(m.Listener, "l1"), db)
    assert stat.S_IMODE(os.stat(out["ssl_cert_path"]).st_mode) == 0o600
