from services.load_balancer.app.drivers import ovn_l4


def test_floating_ip_is_also_a_vip(monkeypatch):
    calls = []
    monkeypatch.setattr(ovn_l4, "_nb", lambda *a, **k: calls.append(a) or "")
    lb = {"id": "L", "vip_address": "10.0.0.5", "vip_floating_ip": "172.17.0.9"}
    ls = {"id": "S", "protocol": "TCP", "protocol_port": 80, "default_pool_id": "P",
          "pools": [{"id": "P", "lb_algorithm": "ROUND_ROBIN", "members": [{"address": "10.0.0.2", "protocol_port": 80, "weight": 1}]}]}
    ovn_l4.OVNL4Driver({}).apply(lb, [ls])
    assert ("set", "load_balancer", "cloudx-ls-S", 'vips:"172.17.0.9:80"="10.0.0.2:80"') in calls


def test_reconcile_attachments_covers_newly_joined_switch(monkeypatch):
    calls = []

    def fake_nb(*args, check=True):
        calls.append(args)
        cmd = args[0]
        if cmd == "ls-list":
            return "sw-vip\nsw-new"
        if cmd == "lsp-list":
            return "lsp-vip-router" if args[1] == "sw-vip" else "lsp-new-router"
        if cmd == "lr-list":
            return "r1"
        if cmd == "lrp-list":
            return "lrp-vip\nlrp-new"
        if cmd == "get" and args[1] == "logical_switch_port":
            return '"lrp-vip"' if args[2] == "lsp-vip-router" else '"lrp-new"'
        if cmd == "get" and args[1] == "logical_router_port":
            return "[]"
        return ""

    monkeypatch.setattr(ovn_l4, "_nb", fake_nb)
    lb = {"id": "L", "vip_address": "10.0.0.5", "vip_switch": "sw-vip"}
    ls = {"id": "S", "protocol": "TCP", "protocol_port": 80}
    ovn_l4.OVNL4Driver({}).reconcile_attachments(lb, [ls])
    assert ("--may-exist", "ls-lb-add", "sw-new", "cloudx-ls-S") in calls