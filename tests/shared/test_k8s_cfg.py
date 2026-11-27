import pytest

from shared.k8s_cfg import render_master, render_worker

CB = "http://172.17.0.1:8015"
M = dict(cluster_id="cls-abc123", name="prod-1", join_token="jt_0123456789abcdef", admin_token="at_0123456789abcdef",
         callback_url=CB, callback_token="ct_0123456789abcdef")


def test_master_cloud_config():
    out = render_master(**M)
    assert out.startswith("#cloud-config\n")
    assert "k3s server --token jt_0123456789abcdef" in out
    assert "token-auth-file=/etc/rancher/k3s/token-auth" in out
    assert "at_0123456789abcdef,admin,admin,system:masters" in out
    assert f"{CB}/internal/k8s-engine/clusters/cls-abc123/ready" in out
    assert "X-Cluster-Token: ct_0123456789abcdef" in out
    assert "X-Internal-Token" not in out
    assert "/readyz" in out


def test_worker_cloud_config():
    out = render_worker("cls-abc123", "10.0.0.7", "jt_0123456789abcdef", CB, "ct_0123456789abcdef")
    assert out.startswith("#cloud-config\n")
    assert "k3s agent --server https://10.0.0.7:6443 --token jt_0123456789abcdef" in out
    assert f"{CB}/internal/k8s-engine/clusters/cls-abc123/ready" in out
    assert "X-Cluster-Token: ct_0123456789abcdef" in out
    assert "X-Internal-Token" not in out
    assert '\\"role\\":\\"worker\\"' in out
    assert '\\"role\\":\\"master\\"' in render_master(**M)


@pytest.mark.parametrize("field,bad", [
    ("name", "Bad Name"), ("name", "a;rm -rf /"), ("cluster_id", "cls-1; id"),
    ("join_token", "a'b"), ("join_token", "a\nb"), ("admin_token", "a b"), ("callback_token", "x$(id)"),
    ("callback_url", "http://x;id"),
])
def test_master_rejects_unsafe_values(field, bad):
    with pytest.raises(ValueError):
        render_master(**{**M, field: bad})


def test_worker_rejects_unsafe_values():
    with pytest.raises(ValueError):
        render_worker("cls-1", "10.0.0.7; id", "jt_0123456789abcdef", CB, "ct_0123456789abcdef")
    with pytest.raises(ValueError):
        render_worker("cls-1", "10.0.0.7", "a'b", CB, "ct_0123456789abcdef")


def test_boot_scripts_wait_for_k3s_so_systemd_run_does_not_kill_it():
    from shared.k8s_cfg import render_master, render_worker
    m = render_master('cls-1', 'c1', 'j' * 16, 'a' * 16, 'http://10.0.0.1:8015', 't' * 16)
    w = render_worker('cls-1', '10.0.0.5', 'j' * 16, 'http://10.0.0.1:8015', 't' * 16)
    for cfg in (m, w):
        assert cfg.index('      wait') > cfg.index('/ready')
        assert 'systemd-run' in cfg and 'nohup' not in cfg
        assert '--property=Delegate=yes' in cfg  # kubelet needs the cgroup subtree, else kubepods is missing


# --- the worker only reports ready once its own node is Ready in the cluster ---------------------------------------
import os
import stat
import subprocess
import textwrap


def _boot_script(cfg: str) -> str:
    """The body of /usr/local/bin/cloudx-k3s-boot.sh from a rendered cloud-config."""
    lines = cfg.split("    content: |\n", 1)[1].split("runcmd:", 1)[0].splitlines()
    return textwrap.dedent("\n".join(lines)) + "\n"


def _run_boot_script(tmp_path, cfg, ready_after):
    """Run the worker boot script with stand-ins for k3s, curl and sleep. `k3s kubectl` answers False until it was asked
    `ready_after` times, then True (a node that never joins: ready_after=None). Returns (kubectl asks, curl calls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "asked"
    state.write_text("0")
    curl_log = tmp_path / "curl.log"
    stubs = {
        "k3s": f"""#!/bin/bash
case "$1" in
  agent) exit 0 ;;
  kubectl)
    n=$(( $(cat {state}) + 1 )); echo $n > {state}
    echo "$@" >> {tmp_path}/kubectl.log
    if [ "{ready_after}" != "None" ] && [ $n -ge {ready_after if ready_after is not None else 0} ]; then echo True; else echo False; fi ;;
esac
""",
        "curl": f"#!/bin/bash\necho \"$@\" >> {curl_log}\nexit 0\n",
        "sleep": "#!/bin/bash\nexit 0\n",
    }
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "boot.sh"
    script.write_text(_boot_script(cfg))
    subprocess.run(["bash", str(script)], env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, check=True, timeout=30)
    calls = curl_log.read_text().splitlines() if curl_log.exists() else []
    return int(state.read_text()), calls


WORKER = ("cls-abc123", "10.0.0.7", "jt_0123456789abcdef", CB, "ct_0123456789abcdef")


def test_the_worker_reports_ready_only_after_its_node_is_ready(tmp_path):
    asked, curl_calls = _run_boot_script(tmp_path, render_worker(*WORKER), ready_after=3)

    assert asked == 3                      # it kept asking until the node answered Ready
    assert len(curl_calls) == 1 and "/clusters/cls-abc123/ready" in curl_calls[0]


def test_a_worker_whose_node_never_joins_never_reports_ready(tmp_path):
    asked, curl_calls = _run_boot_script(tmp_path, render_worker(*WORKER), ready_after=None)

    assert asked >= 100                    # about ten minutes of five-second checks, then it gives up
    assert curl_calls == []                # the cluster then ends in ERROR at the ready timeout, not in a false ACTIVE


def test_the_worker_asks_its_own_node_through_the_kubelet_credentials(tmp_path):
    _run_boot_script(tmp_path, render_worker(*WORKER), ready_after=1)

    asked = (tmp_path / "kubectl.log").read_text()
    assert "--kubeconfig /var/lib/rancher/k3s/agent/kubelet.kubeconfig" in asked
    assert "get node" in asked and "Ready" in asked


def test_the_worker_script_has_no_fixed_sleep_before_the_callback():
    script = _boot_script(render_worker(*WORKER))
    assert "sleep 20" not in script
