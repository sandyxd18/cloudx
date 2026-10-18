import importlib

import pytest
from fastapi import HTTPException


@pytest.fixture
def lb(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/lb.db")
    import services.load_balancer.app.main as m
    importlib.reload(m)
    m.Base.metadata.create_all(m.engine)
    monkeypatch.setattr(m, "_reapply", lambda db, lb: None)
    with m.SessionLocal() as db:
        db.add(m.LoadBalancer(id="lb1", name="lb1", project_id="p1", lb_type="ALB", vip_address="10.0.0.5", provisioning_status="ACTIVE"))
        db.add(m.Listener(id="ls1", load_balancer_id="lb1", name="ls1", protocol="HTTP", protocol_port=80))
        db.add(m.Pool(id="pool2", load_balancer_id="lb1", listener_id="ls1", name="p2", protocol="HTTP", lb_algorithm="ROUND_ROBIN"))
        db.commit()
    m.user = {"sub": "u1", "project_id": "p1", "role": "member"}
    return m


def _pol(m, db, **kw):
    return m.create_l7policy("ls1", m.L7PolicyIn(name="x", **kw), m.user, db)


def test_policy_actions_and_validation(lb):
    m = lb
    with m.SessionLocal() as db:
        p = _pol(m, db, action="REDIRECT_TO_URL", redirect_url="https://x.io/n", redirect_http_code=301)
        assert p["redirect_http_code"] == 301 and p["redirect_pool_id"] is None
        assert _pol(m, db, action="REDIRECT_PREFIX", redirect_prefix="https://x.io")["redirect_http_code"] == 302
        with pytest.raises(HTTPException):  # pool of another LB
            _pol(m, db, redirect_pool_id="nope")
    for bad in ({"action": "REDIRECT_TO_URL"}, {"action": "REJECT", "redirect_url": "https://x.io"},
                {"action": "REDIRECT_TO_URL", "redirect_url": "https://x.io/%[req.hdr(a)]"},
                {"action": "REDIRECT_TO_URL", "redirect_url": "https://x.io", "redirect_http_code": 200}):
        with pytest.raises(ValueError):
            m.L7PolicyIn(name="x", **bad)


def test_policy_put_switches_action_and_toggles_admin_state(lb):
    m = lb
    with m.SessionLocal() as db:
        p = _pol(m, db, action="REDIRECT_TO_URL", redirect_url="https://x.io/n")
        u = m.update_l7policy(p["id"], m.L7PolicyUpdate(action="REJECT"), m.user, db)
        assert u["action"] == "REJECT" and u["redirect_url"] is None
        u = m.update_l7policy(p["id"], m.L7PolicyUpdate(action="REDIRECT_TO_POOL", redirect_pool_id="pool2", admin_state_up=False), m.user, db)
        assert u["redirect_pool_id"] == "pool2" and u["admin_state_up"] is False
        assert m.show_l7policy(p["id"], m.user, db)["id"] == p["id"]
        with pytest.raises(HTTPException) as e:  # the new action has no target
            m.update_l7policy(p["id"], m.L7PolicyUpdate(action="REDIRECT_TO_URL"), m.user, db)
        assert e.value.status_code == 422


def test_rule_validation_and_put(lb):
    m = lb
    for bad in ({"type": "HEADER", "value": "v"}, {"type": "PATH", "value": "/a", "key": "k"},
                {"type": "PATH", "value": "^/a", "compare_type": "STARTS_WITH"},
                {"type": "FILE_TYPE", "value": "jpg", "compare_type": "STARTS_WITH"},
                {"type": "COOKIE", "value": "v", "key": "a b"}):
        with pytest.raises(ValueError):
            m.L7RuleIn(**bad)
    with m.SessionLocal() as db:
        p = _pol(m, db, action="REJECT")
        r = m.create_l7rule(p["id"], m.L7RuleIn(type="HEADER", key="X-A", value="1", compare_type="CONTAINS", invert=True), m.user, db)
        assert r["invert"] is True and r["key"] == "X-A"
        u = m.update_l7rule(p["id"], r["id"], m.L7RuleUpdate(type="PATH", key=None, value="/z"), m.user, db)
        assert u["type"] == "PATH" and u["key"] is None and u["value"] == "/z"
        with pytest.raises(HTTPException) as e:
            m.update_l7rule(p["id"], r["id"], m.L7RuleUpdate(type="COOKIE"), m.user, db)
        assert e.value.status_code == 422
        assert len(m.list_l7rules(p["id"], m.user, db)["rules"]) == 1
        m.delete_l7rule(p["id"], r["id"], m.user, db)
        with pytest.raises(HTTPException):
            m.show_l7rule(p["id"], r["id"], m.user, db)

def test_disabled_policy_and_rule_stay_out_of_the_rendered_tree(lb):
    m = lb
    with m.SessionLocal() as db:
        p = _pol(m, db, action="REJECT")
        r = m.create_l7rule(p["id"], m.L7RuleIn(type="PATH", value="/a"), m.user, db)
        lbo = db.get(m.LoadBalancer, "lb1")
        assert len(m.lb_state(db, lbo)[0]["policies"][0]["rules"]) == 1
        m.update_l7rule(p["id"], r["id"], m.L7RuleUpdate(admin_state_up=False), m.user, db)
        assert m.lb_state(db, lbo)[0]["policies"][0]["rules"] == []
        m.update_l7policy(p["id"], m.L7PolicyUpdate(admin_state_up=False), m.user, db)
        assert m.lb_state(db, lbo)[0]["policies"] == []
def _ca_pem(with_crl=False, crl_days=1):
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    if not with_crl:
        return pem
    crl = (x509.CertificateRevocationListBuilder().issuer_name(name).last_update(now - timedelta(days=3)).next_update(now + timedelta(days=crl_days))
           .sign(key, hashes.SHA256()))
    return pem, crl.public_bytes(serialization.Encoding.PEM).decode()


def test_ssl_rules_validation(lb):
    m = lb
    for ok in ({"type": "SSL_CONN_HAS_CERT", "compare_type": "EQUAL_TO", "value": "true"},
               {"type": "SSL_VERIFY_RESULT", "compare_type": "EQUAL_TO", "value": "0"},
               {"type": "SSL_DN_FIELD", "compare_type": "EQUAL_TO", "value": "alice", "key": "CN"}):
        m.L7RuleIn(**ok)
    for bad in ({"type": "SSL_CONN_HAS_CERT", "compare_type": "STARTS_WITH", "value": "true"},
                {"type": "SSL_CONN_HAS_CERT", "compare_type": "EQUAL_TO", "value": "maybe"},
                {"type": "SSL_VERIFY_RESULT", "compare_type": "EQUAL_TO", "value": "ok"},
                {"type": "SSL_DN_FIELD", "compare_type": "EQUAL_TO", "value": "alice"},
                {"type": "SSL_VERIFY_RESULT", "compare_type": "EQUAL_TO", "value": "0", "key": "CN"}):
        with pytest.raises(ValueError):
            m.L7RuleIn(**bad)


def test_ssl_rule_needs_https_listener_and_listener_needs_ca(lb):
    m = lb
    with m.SessionLocal() as db:
        p = _pol(m, db, action="REJECT")
        with pytest.raises(HTTPException) as e:  # ls1 is plain HTTP
            m.create_l7rule(p["id"], m.L7RuleIn(type="SSL_CONN_HAS_CERT", compare_type="EQUAL_TO", value="true"), m.user, db)
        assert e.value.status_code == 400
    ca = _ca_pem()
    base = {"name": "s", "protocol": "HTTPS", "protocol_port": 443, "certificate_id": "c1"}
    m.ListenerIn(**base, client_authentication="MANDATORY", client_ca_pem=ca)
    for bad in ({"client_authentication": "MANDATORY"}, {"client_ca_pem": ca},
                {"client_authentication": "MANDATORY", "client_ca_pem": "not a pem"},
                {"client_authentication": "MANDATORY", "client_ca_pem": ca, "protocol": "HTTP"},
                {"client_authentication": "MANDATORY", "client_ca_pem": ca, "certificate_id": None}):
        with pytest.raises(ValueError):
            m.ListenerIn(**{**base, **bad})


def test_client_ca_reaches_the_rendered_tree(lb):
    m = lb
    ca = _ca_pem()
    with m.SessionLocal() as db:
        m.db_cert = m.LBCertificate(id="c1", load_balancer_id="lb1", name="c", certificate_pem="x", private_key_pem="y")
        db.add(m.db_cert); db.commit()
        out = m.create_listener("lb1", m.ListenerIn(name="s", protocol="HTTPS", protocol_port=443, certificate_id="c1",
                                                    client_authentication="OPTIONAL", client_ca_pem=ca), m.user, db)
        assert out["client_authentication"] == "OPTIONAL" and out["client_ca_path"].endswith("-ca.pem") and "client_ca_pem" not in out
        ls = next(l for l in m.lb_state(db, db.get(m.LoadBalancer, "lb1"), with_pem=True) if l["id"] == out["id"])
        assert ls["client_ca_pem"] == ca


def test_client_crl_validation_state_and_update(lb):
    m = lb
    ca, crl = _ca_pem(with_crl=True)
    base = {"name": "s", "protocol": "HTTPS", "protocol_port": 443, "certificate_id": "c1",
            "client_authentication": "MANDATORY", "client_ca_pem": ca}
    m.ListenerIn(**base, client_crl_pem=crl)
    for bad in ({"client_crl_pem": "not a crl"}, {"client_crl_pem": crl, "client_ca_pem": None, "client_authentication": "NONE"}):
        with pytest.raises(ValueError):
            m.ListenerIn(**{**base, **bad})
    with m.SessionLocal() as db:
        db.add(m.LBCertificate(id="c1", load_balancer_id="lb1", name="c", certificate_pem="x", private_key_pem="y")); db.commit()
        out = m.create_listener("lb1", m.ListenerIn(**base, client_crl_pem=crl), m.user, db)
        assert out["has_client_crl"] and out["client_crl_path"].endswith("-crl.pem") and "client_crl_pem" not in out
        state = lambda: next(l for l in m.lb_state(db, db.get(m.LoadBalancer, "lb1"), with_pem=True) if l["id"] == out["id"])
        assert state()["client_crl_pem"] == crl
        # mode-only update keeps the CRL; null removes it; garbage is a 400; NONE drops CA and CRL
        m.update_listener(out["id"], m.ListenerUpdate(client_authentication="OPTIONAL"), m.user, db)
        assert state()["client_crl_pem"] == crl
        m.update_listener(out["id"], m.ListenerUpdate(client_authentication="OPTIONAL", client_crl_pem=None), m.user, db)
        assert not state().get("client_crl_pem") and state()["client_crl_path"] is None
        with pytest.raises(HTTPException) as e:
            m.update_listener(out["id"], m.ListenerUpdate(client_authentication="OPTIONAL", client_crl_pem="junk"), m.user, db)
        assert e.value.status_code == 400
        m.update_listener(out["id"], m.ListenerUpdate(client_authentication="OPTIONAL", client_crl_pem=crl), m.user, db)
        m.update_listener(out["id"], m.ListenerUpdate(client_authentication="NONE"), m.user, db)
        assert not state().get("client_crl_pem") and not state().get("client_ca_pem")


def test_list_ignores_all_tenants_for_regular_users(lb):
    m = lb
    with m.SessionLocal() as db:
        db.add(m.LoadBalancer(id="lb2", name="other", project_id="p2", lb_type="ALB", vip_address="10.0.0.6", provisioning_status="ACTIVE"))
        db.commit()
        ids = lambda u, **kw: {x["id"] for x in m.list_load_balancers(u, db, **kw)["load_balancers"]}
        assert ids(m.user) == {"lb1"}
        assert ids({**m.user, "role": "admin"}) == {"lb1", "lb2"}
    import inspect
    assert "all_tenants" not in inspect.signature(m.list_load_balancers).parameters


def test_expired_crl_is_refused_and_next_update_is_shown(lb):
    m = lb
    ca, old = _ca_pem(with_crl=True, crl_days=-1)
    base = {"name": "s", "protocol": "HTTPS", "protocol_port": 443, "certificate_id": "c1",
            "client_authentication": "MANDATORY", "client_ca_pem": ca}
    with pytest.raises(ValueError, match="expired"):
        m.ListenerIn(**base, client_crl_pem=old)
    ca, ok = _ca_pem(with_crl=True)
    with m.SessionLocal() as db:
        db.add(m.LBCertificate(id="c1", load_balancer_id="lb1", name="c", certificate_pem="x", private_key_pem="y")); db.commit()
        out = m.create_listener("lb1", m.ListenerIn(**base, client_crl_pem=ok), m.user, db)
        assert out["client_crl_next_update"]
        with pytest.raises(HTTPException) as e:
            m.update_listener(out["id"], m.ListenerUpdate(client_authentication="MANDATORY", client_crl_pem=old), m.user, db)
        assert e.value.status_code == 400 and "expired" in e.value.detail
