import pytest

from shared.k8s_cfg import render_master, render_worker

CB = "http://172.17.0.1:8015"
M = dict(cluster_id="cls-abc123", name="prod-1", join_token="jt_0123456789abcdef", admin_token="at_0123456789abcdef",
         callback_url=CB, internal_token="it_0123456789abcdef")


def test_master_cloud_config():
    out = render_master(**M)
    assert out.startswith("#cloud-config\n")
    assert "k3s server --token jt_0123456789abcdef" in out
    assert "token-auth-file=/etc/rancher/k3s/token-auth" in out
    assert "at_0123456789abcdef,admin,admin,system:masters" in out
    assert f"{CB}/internal/k8s-engine/clusters/cls-abc123/ready" in out
    assert "X-Internal-Token: it_0123456789abcdef" in out
    assert "/readyz" in out


def test_worker_cloud_config():
    out = render_worker("cls-abc123", "10.0.0.7", "jt_0123456789abcdef", CB, "it_0123456789abcdef")
    assert out.startswith("#cloud-config\n")
    assert "k3s agent --server https://10.0.0.7:6443 --token jt_0123456789abcdef" in out
    assert f"{CB}/internal/k8s-engine/clusters/cls-abc123/ready" in out
    assert "X-Internal-Token: it_0123456789abcdef" in out
    assert '\\"role\\":\\"worker\\"' in out
    assert '\\"role\\":\\"master\\"' in render_master(**M)


@pytest.mark.parametrize("field,bad", [
    ("name", "Bad Name"), ("name", "a;rm -rf /"), ("cluster_id", "cls-1; id"),
    ("join_token", "a'b"), ("join_token", "a\nb"), ("admin_token", "a b"), ("internal_token", "x$(id)"),
    ("callback_url", "http://x;id"),
])
def test_master_rejects_unsafe_values(field, bad):
    with pytest.raises(ValueError):
        render_master(**{**M, field: bad})


def test_worker_rejects_unsafe_values():
    with pytest.raises(ValueError):
        render_worker("cls-1", "10.0.0.7; id", "jt_0123456789abcdef", CB, "it_0123456789abcdef")
    with pytest.raises(ValueError):
        render_worker("cls-1", "10.0.0.7", "a'b", CB, "it_0123456789abcdef")


def test_boot_scripts_wait_for_k3s_so_systemd_run_does_not_kill_it():
    from shared.k8s_cfg import render_master, render_worker
    m = render_master('cls-1', 'c1', 'j' * 16, 'a' * 16, 'http://10.0.0.1:8015', 't' * 16)
    w = render_worker('cls-1', '10.0.0.5', 'j' * 16, 'http://10.0.0.1:8015', 't' * 16)
    for cfg in (m, w):
        assert cfg.index('      wait') > cfg.index('/ready')
        assert 'systemd-run' in cfg and 'nohup' not in cfg
        assert '--property=Delegate=yes' in cfg  # kubelet needs the cgroup subtree, else kubepods is missing
