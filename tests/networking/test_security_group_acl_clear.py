from types import SimpleNamespace


def test_apply_acls_clears_the_port_group_with_an_acl_del_that_ovn_accepts(monkeypatch):
    # ovn-nbctl's acl-del has no --if-exists option (24.03: "'acl-del' command has no '--if-exists' option"), so the
    # old call failed every time, the error was swallowed, and ACLs of deleted rules stayed active in OVN.
    from services.networking.app import ovn_cli

    calls = []
    monkeypatch.setattr(ovn_cli, "ensure_security_group_port_group", lambda group: "pg_x")
    monkeypatch.setattr(ovn_cli, "ovn", lambda *args, check=True: calls.append((args, check)) or "")

    class NoRules:
        def all(self):
            return []

    class FakeDb:
        def scalars(self, statement):
            return NoRules()

    ovn_cli.apply_security_group_acls(FakeDb(), SimpleNamespace(id="sg-id"))

    assert calls[0] == (("--type=port-group", "acl-del", "pg_x"), True)
    assert not any("--if-exists" in args for args, _ in calls)
