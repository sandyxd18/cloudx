
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
