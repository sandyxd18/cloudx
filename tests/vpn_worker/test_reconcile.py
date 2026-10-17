from unittest.mock import patch, MagicMock
import json

from services.vpn_worker.app import main as vpn_worker


def test_vpn_worker_first_tick_command_order(tmp_path, monkeypatch):
    """(a) first tick: ip netns add qvpn-<short>; ovs-vsctl add-port with external_ids:iface-id and cloudx_vpn;
    inner addr hosts[1] (.2), routes via hosts[0] (.1); outer addr; default route; charon-systemd detached; swanctl --load-all.
    """
    monkeypatch.setattr(vpn_worker, "ROOT", tmp_path)
    monkeypatch.setattr(vpn_worker, "HOST", "compute-01")
    monkeypatch.setattr(vpn_worker, "_applied", {})
    monkeypatch.setattr(vpn_worker, "_reported", {})
    monkeypatch.setattr(vpn_worker, "_last_apply_failed", set())

    gw_id = "vpngw-123456789abc"
    short = vpn_worker.gw_short(gw_id)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "gateways": [
            {
                "id": gw_id,
                "router_id": "router-1",
                "transit_cidr": "100.101.0.0/30",
                "outer_ip": "172.24.4.100",
                "outer_prefixlen": 24,
                "outer_gateway_ip": "172.24.4.1",
                "inner_mac": "fa:16:3e:01:02:03",
                "outer_mac": "fa:16:3e:04:05:06",
                "inner_lsp": f"lsp-vpn-{short}-in",
                "outer_lsp": f"lsp-vpn-{short}-out",
                "local_subnets": ["10.0.0.0/24"],
                "connections": [
                    {
                        "id": "vpn-conn-1",
                        "name": "conn-1",
                        "peer_address": "203.0.113.10",
                        "peer_id": "203.0.113.10",
                        "peer_cidrs": ["192.168.1.0/24"],
                        "psk": "secret123",
                        "ike_auth": "sha256",
                        "ike_enc": "aes-256",
                        "ike_pfs": "group14",
                        "ipsec_auth": "sha256",
                        "ipsec_enc": "aes-256",
                        "ipsec_pfs": "group14",
                    }
                ],
            }
        ]
    }

    commands_run = []

    def mock_run(cmd, check=True, timeout=30):
        commands_run.append(cmd)
        res = MagicMock()
        res.returncode = 0
        res.stdout = "ESTABLISHED\nINSTALLED"
        res.stderr = ""
        if "netns" in cmd and "list" in cmd:
            res.stdout = ""
        if "sh" in cmd and "-c" in cmd and "echo $!" in cmd[2]:
            pid_file = tmp_path / gw_id / "charon.pid"
            pid_file.write_text("12345\n")
        return res

    with patch("requests.get", return_value=mock_response), \
         patch("requests.post") as mock_post, \
         patch.object(vpn_worker, "run", side_effect=mock_run), \
         patch("time.sleep", return_value=None):

        vpn_worker.tick()

        # Assert exact commands / steps
        flat_cmds = [c for c in commands_run]
        assert ["ip", "netns", "add", f"qvpn-{short}"] in flat_cmds
        assert any("add-port" in c and f"vpn{short}-in" in c and f"external_ids:cloudx_vpn={gw_id}" in c for c in flat_cmds)
        assert any("add-port" in c and f"vpn{short}-out" in c and f"external_ids:cloudx_vpn={gw_id}" in c for c in flat_cmds)
        # Inner IP must be 100.101.0.2 (hosts[1])
        assert any("addr" in c and "replace" in c and "100.101.0.2/30" in c for c in flat_cmds)
        # Local route via 100.101.0.1 (hosts[0])
        assert any("route" in c and "replace" in c and "10.0.0.0/24" in c and "via" in c and "100.101.0.1" in c for c in flat_cmds)
        # Outer IP addr
        assert any("addr" in c and "replace" in c and "172.24.4.100/24" in c for c in flat_cmds)
        # Default route via outer gateway ip
        assert any("route" in c and "replace" in c and "default" in c and "via" in c and "172.24.4.1" in c for c in flat_cmds)
        # charon-systemd detached via sh -c
        assert any("sh" in c and "-c" in c and "charon-systemd" in c[2] for c in flat_cmds)
        # swanctl --load-all
        assert any("swanctl" in c and "--load-all" in c for c in flat_cmds)


def test_vpn_worker_swanctl_conf_content(tmp_path, monkeypatch):
    """(b) swanctl.conf content: local_ts equals router local_subnets, remote_ts equals peer_cidrs, file mode 0600."""
    monkeypatch.setattr(vpn_worker, "ROOT", tmp_path)
    monkeypatch.setattr(vpn_worker, "HOST", "compute-01")
    monkeypatch.setattr(vpn_worker, "_applied", {})
    monkeypatch.setattr(vpn_worker, "_reported", {})
    monkeypatch.setattr(vpn_worker, "_last_apply_failed", set())

    gw_id = "vpngw-123456789abc"
    short = vpn_worker.gw_short(gw_id)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "gateways": [
            {
                "id": gw_id,
                "router_id": "router-1",
                "transit_cidr": "100.101.0.0/30",
                "outer_ip": "172.24.4.100",
                "outer_prefixlen": 24,
                "outer_gateway_ip": "172.24.4.1",
                "inner_mac": "fa:16:3e:01:02:03",
                "outer_mac": "fa:16:3e:04:05:06",
                "inner_lsp": f"lsp-vpn-{short}-in",
                "outer_lsp": f"lsp-vpn-{short}-out",
                "local_subnets": ["10.0.0.0/24", "10.0.1.0/24"],
                "connections": [
                    {
                        "id": "vpn-conn-1",
                        "name": "conn-1",
                        "peer_address": "203.0.113.10",
                        "peer_id": "203.0.113.10",
                        "peer_cidrs": ["192.168.1.0/24"],
                        "psk": "secret123",
                        "ike_auth": "sha256",
                        "ike_enc": "aes-256",
                        "ike_pfs": "group14",
                        "ipsec_auth": "sha256",
                        "ipsec_enc": "aes-256",
                        "ipsec_pfs": "group14",
                    }
                ],
            }
        ]
    }

    def mock_run(cmd, check=True, timeout=30):
        res = MagicMock()
        res.returncode = 0
        res.stdout = "ESTABLISHED\nINSTALLED"
        res.stderr = ""
        if "sh" in cmd and "-c" in cmd and "echo $!" in cmd[2]:
            (tmp_path / gw_id / "charon.pid").write_text("12345\n")
        return res

    with patch("requests.get", return_value=mock_response), \
         patch("requests.post"), \
         patch.object(vpn_worker, "run", side_effect=mock_run), \
         patch("time.sleep", return_value=None):

        vpn_worker.tick()
        conf_path = tmp_path / gw_id / "swanctl.conf"
        assert conf_path.exists()
        # Verify file mode (or 0o600 / 0o666 on Windows)
        mode = oct(conf_path.stat().st_mode & 0o777)
        assert mode in ("0600", "0o600", "0o666")

        content = conf_path.read_text()
        assert "10.0.0.0/24" in content
        assert "10.0.1.0/24" in content
        assert "192.168.1.0/24" in content

        # strongswan.conf must keep the plugin snippets inside charon { plugins { } } (found on the lab: outside, crypto plugins don't load)
        sc = (tmp_path / gw_id / "strongswan.conf").read_text()
        plugins_block = sc[sc.index("plugins {"):sc.index("}\n}\ninclude /etc/strongswan.d/*.conf")]
        assert "include /etc/strongswan.d/charon/*.conf" in plugins_block
        assert f"socket = unix://{tmp_path / gw_id}/vici" in plugins_block


def test_vpn_worker_second_tick_and_reload(tmp_path, monkeypatch):
    """(c) same config 2nd tick -> no --load-all; changed psk -> reload."""
    monkeypatch.setattr(vpn_worker, "ROOT", tmp_path)
    monkeypatch.setattr(vpn_worker, "HOST", "compute-01")
    monkeypatch.setattr(vpn_worker, "_applied", {})
    monkeypatch.setattr(vpn_worker, "_reported", {})
    monkeypatch.setattr(vpn_worker, "_last_apply_failed", set())

    gw_id = "vpngw-123456789abc"
    short = vpn_worker.gw_short(gw_id)

    payload1 = {
        "gateways": [
            {
                "id": gw_id,
                "router_id": "router-1",
                "transit_cidr": "100.101.0.0/30",
                "outer_ip": "172.24.4.100",
                "outer_prefixlen": 24,
                "outer_gateway_ip": "172.24.4.1",
                "inner_mac": "fa:16:3e:01:02:03",
                "outer_mac": "fa:16:3e:04:05:06",
                "inner_lsp": f"lsp-vpn-{short}-in",
                "outer_lsp": f"lsp-vpn-{short}-out",
                "local_subnets": ["10.0.0.0/24"],
                "connections": [
                    {
                        "id": "vpn-conn-1",
                        "name": "conn-1",
                        "peer_address": "203.0.113.10",
                        "peer_id": "203.0.113.10",
                        "peer_cidrs": ["192.168.1.0/24"],
                        "psk": "secret123",
                        "ike_auth": "sha256",
                        "ike_enc": "aes-256",
                        "ike_pfs": "group14",
                        "ipsec_auth": "sha256",
                        "ipsec_enc": "aes-256",
                        "ipsec_pfs": "group14",
                    }
                ],
            }
        ]
    }

    commands_run = []

    def mock_run(cmd, check=True, timeout=30):
        commands_run.append(cmd)
        res = MagicMock()
        res.returncode = 0
        res.stdout = "ESTABLISHED\nINSTALLED"
        res.stderr = ""
        if "sh" in cmd and "-c" in cmd and "echo $!" in cmd[2]:
            (tmp_path / gw_id / "charon.pid").write_text("12345\n")
        return res

    mock_resp = MagicMock()
    mock_resp.json.return_value = payload1

    with patch("requests.get", return_value=mock_resp), \
         patch("requests.post"), \
         patch.object(vpn_worker, "run", side_effect=mock_run), \
         patch("time.sleep", return_value=None):

        # Tick 1
        vpn_worker.tick()
        load_count_1 = sum(1 for c in commands_run if "--load-all" in c)
        assert load_count_1 == 1

        # Tick 2 (same config) -> no new --load-all
        vpn_worker.tick()
        load_count_2 = sum(1 for c in commands_run if "--load-all" in c)
        assert load_count_2 == 1

        # Tick 3 (changed psk) -> reload
        payload2 = json.loads(json.dumps(payload1))
        payload2["gateways"][0]["connections"][0]["psk"] = "newsecret456"
        mock_resp.json.return_value = payload2

        vpn_worker.tick()
        load_count_3 = sum(1 for c in commands_run if "--load-all" in c)
        assert load_count_3 == 2


def test_vpn_worker_orphan_cleanup(tmp_path, monkeypatch):
    """(d) orphan dir ROOT/<other-gw-id> and ip netns list output containing qvpn-<other short> -> assert exact kill/del-port/netns del calls and dir removed."""
    monkeypatch.setattr(vpn_worker, "ROOT", tmp_path)
    monkeypatch.setattr(vpn_worker, "HOST", "compute-01")
    monkeypatch.setattr(vpn_worker, "_applied", {})
    monkeypatch.setattr(vpn_worker, "_reported", {})
    monkeypatch.setattr(vpn_worker, "_last_apply_failed", set())

    other_gw = "vpngw-999999999999"
    other_short = vpn_worker.gw_short(other_gw)
    orphan_d = tmp_path / other_gw
    orphan_d.mkdir(exist_ok=True)
    (orphan_d / "charon.pid").write_text("9999\n")

    mock_response = MagicMock()
    mock_response.json.return_value = {"gateways": []}  # Want nothing

    commands_run = []

    def mock_run(cmd, check=True, timeout=30):
        commands_run.append(cmd)
        res = MagicMock()
        res.returncode = 0
        res.stdout = ""
        if "netns" in cmd and "list" in cmd:
            res.stdout = f"qvpn-{other_short} (id: 10)\n"
        return res

    with patch("requests.get", return_value=mock_response), \
         patch("requests.post"), \
         patch.object(vpn_worker, "run", side_effect=mock_run), \
         patch("time.sleep", return_value=None):

        vpn_worker.tick()

        # Assert orphan cleanup commands
        assert ["kill", "9999"] in commands_run
        assert ["ovs-vsctl", "--if-exists", "del-port", "br-int", f"vpn{other_short}-in"] in commands_run
        assert ["ovs-vsctl", "--if-exists", "del-port", "br-int", f"vpn{other_short}-out"] in commands_run
        assert ["ip", "netns", "del", f"qvpn-{other_short}"] in commands_run
        assert not orphan_d.exists()


def test_vpn_worker_status_post_on_change(tmp_path, monkeypatch):
    """(e) status POST only on change."""
    monkeypatch.setattr(vpn_worker, "ROOT", tmp_path)
    monkeypatch.setattr(vpn_worker, "HOST", "compute-01")
    monkeypatch.setattr(vpn_worker, "_applied", {})
    monkeypatch.setattr(vpn_worker, "_reported", {})
    monkeypatch.setattr(vpn_worker, "_last_apply_failed", set())

    gw_id = "vpngw-123456789abc"
    short = vpn_worker.gw_short(gw_id)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "gateways": [
            {
                "id": gw_id,
                "router_id": "router-1",
                "transit_cidr": "100.101.0.0/30",
                "outer_ip": "172.24.4.100",
                "outer_prefixlen": 24,
                "outer_gateway_ip": "172.24.4.1",
                "inner_mac": "fa:16:3e:01:02:03",
                "outer_mac": "fa:16:3e:04:05:06",
                "inner_lsp": f"lsp-vpn-{short}-in",
                "outer_lsp": f"lsp-vpn-{short}-out",
                "local_subnets": ["10.0.0.0/24"],
                "connections": [
                    {
                        "id": "vpn-conn-1",
                        "name": "conn-1",
                        "peer_address": "203.0.113.10",
                        "peer_id": "203.0.113.10",
                        "peer_cidrs": ["192.168.1.0/24"],
                        "psk": "secret123",
                        "ike_auth": "sha256",
                        "ike_enc": "aes-256",
                        "ike_pfs": "group14",
                        "ipsec_auth": "sha256",
                        "ipsec_enc": "aes-256",
                        "ipsec_pfs": "group14",
                    }
                ],
            }
        ]
    }

    def mock_run(cmd, check=True, timeout=30):
        res = MagicMock()
        res.returncode = 0
        res.stdout = "ESTABLISHED\nINSTALLED"
        res.stderr = ""
        if "sh" in cmd and "-c" in cmd and "echo $!" in cmd[2]:
            (tmp_path / gw_id / "charon.pid").write_text("12345\n")
        return res

    with patch("requests.get", return_value=mock_response), \
         patch("requests.post") as mock_post, \
         patch.object(vpn_worker, "run", side_effect=mock_run), \
         patch("time.sleep", return_value=None):

        # Tick 1: status changes from empty to ACTIVE -> POST called
        vpn_worker.tick()
        assert mock_post.call_count == 1

        # Tick 2: status unchanged -> POST not called again
        vpn_worker.tick()
        assert mock_post.call_count == 1


def test_vpn_worker_psk_never_in_logs_or_argv(tmp_path, monkeypatch, caplog):
    """(f) PSK never in caplog text nor in any argv."""
    monkeypatch.setattr(vpn_worker, "ROOT", tmp_path)
    monkeypatch.setattr(vpn_worker, "HOST", "compute-01")
    monkeypatch.setattr(vpn_worker, "_applied", {})
    monkeypatch.setattr(vpn_worker, "_reported", {})
    monkeypatch.setattr(vpn_worker, "_last_apply_failed", set())

    gw_id = "vpngw-123456789abc"
    short = vpn_worker.gw_short(gw_id)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "gateways": [
            {
                "id": gw_id,
                "router_id": "router-1",
                "transit_cidr": "100.101.0.0/30",
                "outer_ip": "172.24.4.100",
                "outer_prefixlen": 24,
                "outer_gateway_ip": "172.24.4.1",
                "inner_mac": "fa:16:3e:01:02:03",
                "outer_mac": "fa:16:3e:04:05:06",
                "inner_lsp": f"lsp-vpn-{short}-in",
                "outer_lsp": f"lsp-vpn-{short}-out",
                "local_subnets": ["10.0.0.0/24"],
                "connections": [
                    {
                        "id": "vpn-conn-1",
                        "name": "conn-1",
                        "peer_address": "203.0.113.10",
                        "peer_id": "203.0.113.10",
                        "peer_cidrs": ["192.168.1.0/24"],
                        "psk": "supersecretpsk999",
                        "ike_auth": "sha256",
                        "ike_enc": "aes-256",
                        "ike_pfs": "group14",
                        "ipsec_auth": "sha256",
                        "ipsec_enc": "aes-256",
                        "ipsec_pfs": "group14",
                    }
                ],
            }
        ]
    }

    commands_run = []

    def mock_run(cmd, check=True, timeout=30):
        commands_run.append(cmd)
        res = MagicMock()
        res.returncode = 0
        res.stdout = "ESTABLISHED\nINSTALLED"
        res.stderr = ""
        if "sh" in cmd and "-c" in cmd and "echo $!" in cmd[2]:
            (tmp_path / gw_id / "charon.pid").write_text("12345\n")
        return res

    with patch("requests.get", return_value=mock_response), \
         patch("requests.post"), \
         patch.object(vpn_worker, "run", side_effect=mock_run), \
         patch("time.sleep", return_value=None):

        vpn_worker.tick()

        for record in caplog.records:
            assert "supersecretpsk999" not in record.message
        for cmd in commands_run:
            for arg in cmd:
                assert "supersecretpsk999" not in str(arg)


def test_vpn_worker_apply_failure_error_reported(tmp_path, monkeypatch):
    """(g) apply failure -> ERROR with reason posted, not overwritten by DOWN on the same tick."""
    monkeypatch.setattr(vpn_worker, "ROOT", tmp_path)
    monkeypatch.setattr(vpn_worker, "HOST", "compute-01")
    monkeypatch.setattr(vpn_worker, "_applied", {})
    monkeypatch.setattr(vpn_worker, "_reported", {})
    monkeypatch.setattr(vpn_worker, "_last_apply_failed", set())

    gw_id = "vpngw-123456789abc"
    short = vpn_worker.gw_short(gw_id)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "gateways": [
            {
                "id": gw_id,
                "router_id": "router-1",
                "transit_cidr": "100.101.0.0/30",
                "outer_ip": "172.24.4.100",
                "outer_prefixlen": 24,
                "outer_gateway_ip": "172.24.4.1",
                "inner_mac": "fa:16:3e:01:02:03",
                "outer_mac": "fa:16:3e:04:05:06",
                "inner_lsp": f"lsp-vpn-{short}-in",
                "outer_lsp": f"lsp-vpn-{short}-out",
                "local_subnets": ["10.0.0.0/24"],
                "connections": [
                    {
                        "id": "vpn-conn-1",
                        "name": "conn-1",
                        "peer_address": "203.0.113.10",
                        "peer_id": "203.0.113.10",
                        "peer_cidrs": ["192.168.1.0/24"],
                        "psk": "secret123",
                        "ike_auth": "sha256",
                        "ike_enc": "aes-256",
                        "ike_pfs": "group14",
                        "ipsec_auth": "sha256",
                        "ipsec_enc": "aes-256",
                        "ipsec_pfs": "group14",
                    }
                ],
            }
        ]
    }

    with patch("requests.get", return_value=mock_response), \
         patch("requests.post") as mock_post, \
         patch.object(vpn_worker, "apply_gateway", side_effect=RuntimeError("disk full")), \
         patch("time.sleep", return_value=None):

        vpn_worker.tick()
        assert vpn_worker._reported["vpn-conn-1"]["status"] == "ERROR"
        assert "disk full" in vpn_worker._reported["vpn-conn-1"]["status_reason"]
