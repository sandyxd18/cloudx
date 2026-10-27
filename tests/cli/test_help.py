def test_cli_service_help(cloudx):
    res = cloudx("service", "--help")
    assert res.returncode == 0
    assert "service" in res.stdout
    assert "status" in res.stdout
    assert "list" in res.stdout


def test_key_manager_cli_help(cloudx):
    res = cloudx("secret", "--help")
    assert res.returncode == 0
    assert "secret" in res.stdout

    res2 = cloudx("secret-container", "--help")
    assert res2.returncode == 0
    assert "secret-container" in res2.stdout

    res3 = cloudx("vpn", "--help")
    assert res3.returncode == 0
    assert "vpn" in res3.stdout


def test_k8s_engine_cli_help(cloudx):
    res = cloudx("k8s-template", "--help")
    assert res.returncode == 0
    assert "k8s-template" in res.stdout

    res2 = cloudx("cluster", "--help")
    assert res2.returncode == 0
    assert "cluster" in res2.stdout


def test_stack_and_alarm_cli_help(cloudx):
    for group, subs in (("stack", ("create", "update", "resources", "events", "suspend")),
                        ("alarm", ("create", "history", "delete"))):
        res = cloudx(group, "--help")
        assert res.returncode == 0
        for s in subs:
            assert s in res.stdout


def test_role_assignment_cli_help(cloudx):
    res = cloudx("role-assignment", "--help")
    assert res.returncode == 0
    assert "role-assignment" in res.stdout
    for s in ("list", "create", "grant", "revoke", "delete"):
        assert s in res.stdout


def test_load_balancer_new_commands_help(cloudx):
    res = cloudx("load-balancer", "--help")
    assert res.returncode == 0
    for s in ("certificate", "l7policy", "l7rule", "listener"):
        assert s in res.stdout

    res_cert = cloudx("load-balancer", "certificate", "--help")
    assert res_cert.returncode == 0
    for s in ("create", "list", "delete"):
        assert s in res_cert.stdout

    res_policy = cloudx("load-balancer", "l7policy", "--help")
    assert res_policy.returncode == 0
    for s in ("create", "list", "delete"):
        assert s in res_policy.stdout

    res_rule = cloudx("load-balancer", "l7rule", "--help")
    assert res_rule.returncode == 0
    for s in ("create", "list", "delete"):
        assert s in res_rule.stdout


def test_network_create_help_has_no_provider_nic_options(cloudx):
    res = cloudx("network", "create", "--help")
    assert res.returncode == 0, res.stderr
    assert "--external" in res.stdout
    assert "--provider-bridge" not in res.stdout
    assert "--provider-cidr" not in res.stdout
