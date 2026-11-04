import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared import policy  # noqa: E402

ADMIN = {"role": "admin"}
DOMAIN_ADMIN = {"role": "member", "domain_admin": True}
MEMBER = {"role": "member"}

ADMIN_OR_DOMAIN_ADMIN_ACTIONS = {
    "identity:user:create", "identity:user:list", "identity:user:delete",
    "identity:domain:list", "identity:domain:show", "identity:domain:quota-show",
    "identity:project:create", "identity:project:update", "identity:project:delete",
    "identity:role-assignment:create", "identity:role-assignment:delete",
}
ADMIN_ONLY_ACTIONS = {
    "identity:domain:create", "identity:domain:update", "identity:domain:delete",
    "identity:domain:quota-set", "identity:domain:quota-clear",
    "identity:service:create", "identity:service:delete",
    "identity:endpoint:create", "identity:endpoint:delete",
    "identity:project:quota-set", "identity:audit-log:list",
}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class PolicyFile:
    """A policy file in tmp_path; every write gets a distinct mtime so tests never sleep."""

    def __init__(self, path):
        self.path = path
        self._tick = 0

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        self._tick += 1
        stamp = 10**18 + self._tick * 10**9
        os.utime(self.path, ns=(stamp, stamp))

    def remove(self):
        self.path.unlink()


@pytest.fixture(autouse=True)
def log_level(caplog):
    caplog.set_level(logging.DEBUG, logger="cloudx-policy")


@pytest.fixture
def pfile(tmp_path, monkeypatch):
    path = tmp_path / "policy.yaml"
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(path))
    policy.reset()
    yield PolicyFile(path)
    policy.reset()


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(policy, "_monotonic", c)
    return c


def messages(caplog, level):
    return [r.getMessage() for r in caplog.records if r.name == "cloudx-policy" and r.levelno == level]


def no_logs(caplog):
    return [r for r in caplog.records if r.name == "cloudx-policy"] == []


def test_defaults_are_the_22_actions_of_the_spec():
    expected = {a: "role:admin or domain_admin:True" for a in ADMIN_OR_DOMAIN_ADMIN_ACTIONS}
    expected.update({a: "role:admin" for a in ADMIN_ONLY_ACTIONS})
    assert len(expected) == 22
    assert policy.DEFAULT_RULES == expected


def test_policy_file_default_path_and_env_read_at_call_time(monkeypatch):
    monkeypatch.delenv("CLOUDX_POLICY_FILE", raising=False)
    assert policy.POLICY_FILE() == "/etc/cloudx/policy.yaml"
    monkeypatch.setenv("CLOUDX_POLICY_FILE", "/x/y.yaml")
    assert policy.POLICY_FILE() == "/x/y.yaml"


def test_no_file_gives_defaults_and_logs_nothing(pfile, caplog):
    assert policy.rules() == policy.DEFAULT_RULES
    assert set(policy.sources().values()) == {"default"}
    assert set(policy.sources()) == set(policy.DEFAULT_RULES)
    assert policy.authorize("identity:domain:create", ADMIN) is True
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    assert no_logs(caplog)


def test_rules_returns_a_copy(pfile):
    policy.rules()["identity:domain:create"] = "@"
    assert policy.rules()["identity:domain:create"] == "role:admin"


def test_file_overrides_one_action_only(pfile, caplog):
    pfile.write("identity:domain:create: role:admin or domain_admin:True\n")
    rules = policy.rules()
    assert rules["identity:domain:create"] == "role:admin or domain_admin:True"
    assert {a: r for a, r in rules.items() if a != "identity:domain:create"} == {
        a: r for a, r in policy.DEFAULT_RULES.items() if a != "identity:domain:create"
    }
    sources = policy.sources()
    assert sources["identity:domain:create"] == "file"
    assert {s for a, s in sources.items() if a != "identity:domain:create"} == {"default"}
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    assert no_logs(caplog)


def test_empty_file_means_no_overrides_and_logs_nothing(pfile, caplog):
    pfile.write("")
    assert policy.rules() == policy.DEFAULT_RULES
    pfile.write("# only a comment\n\n")
    policy.reset()
    assert policy.rules() == policy.DEFAULT_RULES
    assert no_logs(caplog)


def test_null_document_means_no_overrides(pfile, caplog):
    pfile.write("---\n")
    assert policy.rules() == policy.DEFAULT_RULES
    pfile.write("null\n")
    policy.reset()
    assert policy.rules() == policy.DEFAULT_RULES
    assert no_logs(caplog)


@pytest.mark.parametrize("text", ["- identity:domain:create\n- role:admin\n", "just a string\n", "42\n"])
def test_top_level_that_is_not_a_mapping_keeps_defaults_and_logs_error(pfile, caplog, text):
    pfile.write(text)
    assert policy.rules() == policy.DEFAULT_RULES
    assert set(policy.sources().values()) == {"default"}
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "mapping" in errors[0]


def test_non_string_rule_is_a_rule_error_for_that_action(pfile, caplog):
    pfile.write("identity:user:create: true\nidentity:domain:create: 'role:admin or domain_admin:True'\n")
    assert policy.rules()["identity:user:create"] == policy.DEFAULT_RULES["identity:user:create"]
    assert policy.sources()["identity:user:create"] == "default"
    assert policy.sources()["identity:domain:create"] == "file"
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "identity:user:create" in errors[0] and "quote" in errors[0]


@pytest.mark.parametrize("value", ["1", "[role:admin]", "null", "~"])
def test_other_non_string_values_are_rule_errors(pfile, caplog, value):
    pfile.write(f"identity:user:create: {value}\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def test_non_string_key_is_ignored_with_an_error(pfile, caplog):
    pfile.write("123: role:admin\nidentity:domain:create: '@'\n")
    assert policy.sources()["identity:domain:create"] == "file"
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "123" in errors[0]


def test_rule_error_keeps_only_that_actions_default(pfile, caplog):
    pfile.write(
        "identity:user:create: 'role:admin and'\n"
        "identity:domain:create: 'role:admin or domain_admin:True'\n"
    )
    assert policy.rules()["identity:user:create"] == policy.DEFAULT_RULES["identity:user:create"]
    assert policy.sources()["identity:user:create"] == "default"
    assert policy.sources()["identity:domain:create"] == "file"
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "identity:user:create" in errors[0]


def test_unknown_reference_is_a_rule_error(pfile, caplog):
    pfile.write("identity:user:create: 'rule:nope'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "identity:user:create" in errors[0] and "nope" in errors[0]


def test_cycle_is_a_rule_error(pfile, caplog):
    pfile.write("a: 'rule:b'\nb: 'rule:a'\nidentity:user:create: 'rule:a'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert policy.authorize("identity:user:create", ADMIN) is True
    assert policy.authorize("identity:user:create", MEMBER) is False
    errors = messages(caplog, logging.ERROR)
    assert any("rule 'a'" in e and "cycle" in e for e in errors)
    assert any("rule 'b'" in e and "cycle" in e for e in errors)
    # the entries that only existed to be referenced are not warned about as unreferenced
    assert messages(caplog, logging.WARNING) == []


def test_action_cycle_through_actions_falls_back_for_every_member(pfile, caplog):
    pfile.write("identity:user:create: 'rule:identity:user:delete'\nidentity:user:delete: 'rule:identity:user:create'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 2


def test_self_reference_is_a_cycle(pfile, caplog):
    pfile.write("identity:user:create: 'rule:identity:user:create'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def _chain(hops):
    # identity:user:create -> r1 -> ... -> r<hops> -> role:admin, i.e. `hops` references in a row
    lines = ["identity:user:create: 'rule:r1'"]
    lines += [f"r{i}: 'rule:r{i + 1}'" for i in range(1, hops)]
    lines.append(f"r{hops}: 'role:admin'")
    return "\n".join(lines) + "\n"


def test_a_chain_of_11_references_is_a_rule_error(pfile, caplog):
    pfile.write(_chain(11))
    assert policy.sources()["identity:user:create"] == "default"
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "identity:user:create" in errors[0] and "deep" in errors[0]


def test_a_chain_of_10_references_is_fine(pfile, caplog):
    pfile.write(_chain(10))
    assert policy.sources()["identity:user:create"] == "file"
    assert policy.authorize("identity:user:create", ADMIN) is True
    assert policy.authorize("identity:user:create", DOMAIN_ADMIN) is False
    assert messages(caplog, logging.ERROR) == []


def test_very_long_chain_of_named_rules_does_not_crash(pfile, caplog):
    n = 5000
    lines = [f"r{i}: 'rule:r{i + 1}'" for i in range(n)] + [f"r{n}: 'role:admin'"]
    lines.append("identity:user:create: 'rule:r0'")
    pfile.write("\n".join(lines) + "\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert policy.authorize("identity:user:create", ADMIN) is True


def test_deeply_nested_rule_through_many_references_cannot_overflow_the_stack(pfile, caplog):
    deep = "not " * 190 + "role:admin"
    lines = [f"r{i}: '{deep.replace('role:admin', f'rule:r{i + 1}')}'" for i in range(1, 9)]
    lines.append("r9: 'role:admin'")
    lines.append("identity:user:create: 'rule:r1'")
    pfile.write("\n".join(lines) + "\n")
    # whatever is decided about these rules, evaluating must not raise
    policy.authorize("identity:user:create", ADMIN)
    policy.authorize("identity:user:create", MEMBER)


def test_named_rule_can_be_referenced(pfile, caplog):
    pfile.write('admin_required: "role:admin"\nidentity:user:create: "rule:admin_required"\n')
    assert policy.sources()["identity:user:create"] == "file"
    assert policy.rules()["identity:user:create"] == "rule:admin_required"
    assert policy.authorize("identity:user:create", ADMIN) is True
    assert policy.authorize("identity:user:create", DOMAIN_ADMIN) is False
    assert no_logs(caplog)


def test_a_rule_can_reference_another_action(pfile):
    pfile.write('identity:project:create: "rule:identity:domain:create"\n')
    assert policy.authorize("identity:project:create", ADMIN) is True
    assert policy.authorize("identity:project:create", DOMAIN_ADMIN) is False


def test_dropped_named_rule_invalidates_the_rules_that_use_it(pfile, caplog):
    pfile.write(
        "base: 'role:admin and'\n"
        "mid: 'rule:base'\n"
        "identity:user:create: 'rule:mid'\n"
        "identity:domain:create: '@'\n"
    )
    assert policy.sources()["identity:user:create"] == "default"
    assert policy.sources()["identity:domain:create"] == "file"
    errors = messages(caplog, logging.ERROR)
    assert any("base" in e for e in errors)
    assert any("mid" in e for e in errors)
    assert any("identity:user:create" in e for e in errors)
    assert messages(caplog, logging.WARNING) == []


def test_action_falling_back_does_not_break_rules_that_reference_it(pfile):
    pfile.write(
        "identity:user:create: 'rule:nope'\n"
        "identity:user:delete: 'rule:identity:user:create'\n"
    )
    assert policy.sources()["identity:user:create"] == "default"
    assert policy.sources()["identity:user:delete"] == "file"
    assert policy.authorize("identity:user:delete", DOMAIN_ADMIN) is True
    assert policy.authorize("identity:user:delete", MEMBER) is False


def test_unreferenced_unknown_entry_logs_warning(pfile, caplog):
    pfile.write("identity:user:creat: 'role:admin'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    warnings = messages(caplog, logging.WARNING)
    assert len(warnings) == 1 and "identity:user:creat" in warnings[0]
    assert messages(caplog, logging.ERROR) == []


def test_referenced_named_rule_logs_no_warning(pfile, caplog):
    pfile.write('helper: "role:admin"\nidentity:user:create: "rule:helper"\n')
    assert messages(caplog, logging.WARNING) == []
    policy.rules()
    assert no_logs(caplog)


@pytest.mark.parametrize("text", ["identity:domain:create: !\n", "identity:domain:create: @\n"])
def test_unquoted_special_characters_never_change_the_decision(pfile, caplog, text):
    pfile.write(text)
    assert policy.authorize("identity:domain:create", ADMIN) is True
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    assert policy.authorize("identity:domain:create", MEMBER) is False
    assert policy.rules() == policy.DEFAULT_RULES
    assert messages(caplog, logging.ERROR)


def test_invalid_yaml_keeps_defaults_and_logs_error(pfile, caplog):
    pfile.write("identity:domain:create: [unclosed\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def test_unreadable_file_keeps_defaults_and_logs_error(pfile, caplog, monkeypatch):
    pfile.write("identity:domain:create: '@'\n")
    real_open = open

    def refuse(path, *a, **k):
        if str(path) == str(pfile.path):
            raise PermissionError("denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", refuse)
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def test_file_that_is_not_utf8_keeps_defaults_and_logs_error(pfile, caplog):
    pfile.path.write_bytes(b"identity:domain:create: '\xff\xfe'\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def test_yaml_tags_are_not_executed(pfile, caplog):
    pfile.write("identity:domain:create: !!python/object/apply:os.getcwd []\n")
    assert policy.rules() == policy.DEFAULT_RULES
    assert len(messages(caplog, logging.ERROR)) == 1


def test_a_failed_reload_keeps_the_last_good_rules(pfile, clock, caplog):
    pfile.write("identity:domain:create: 'role:admin or domain_admin:True'\n")
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    pfile.write("identity:domain:create: [unclosed\n")
    clock.advance(3)
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    assert policy.sources()["identity:domain:create"] == "file"
    assert len(messages(caplog, logging.ERROR)) == 1


def test_a_failed_reload_is_logged_once_not_on_every_check(pfile, clock, caplog):
    pfile.write("identity:domain:create: '@'\n")
    policy.rules()
    pfile.write("- not a mapping\n")
    for _ in range(5):
        clock.advance(3)
        policy.rules()
    assert len(messages(caplog, logging.ERROR)) == 1
    pfile.write("{broken\n")
    clock.advance(3)
    policy.rules()
    assert len(messages(caplog, logging.ERROR)) == 2


def test_a_valid_file_after_a_broken_one_is_picked_up(pfile, clock):
    pfile.write("{broken\n")
    assert policy.rules() == policy.DEFAULT_RULES
    pfile.write("identity:domain:create: '@'\n")
    clock.advance(3)
    assert policy.rules()["identity:domain:create"] == "@"


def test_mtime_change_is_picked_up_without_restart(pfile, clock):
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    pfile.write("identity:domain:create: 'role:admin or domain_admin:True'\n")
    clock.advance(3)
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True
    pfile.write("identity:domain:create: 'role:admin'\n")
    clock.advance(3)
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False


def test_mtime_is_checked_at_most_every_2_seconds(pfile, clock):
    pfile.write("identity:domain:create: 'role:admin'\n")
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    pfile.write("identity:domain:create: 'role:admin or domain_admin:True'\n")
    clock.advance(1.9)
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is False
    clock.advance(0.2)
    assert policy.authorize("identity:domain:create", DOMAIN_ADMIN) is True


def test_unchanged_mtime_is_not_reparsed_or_relogged(pfile, clock, caplog):
    pfile.write("identity:user:creat: 'role:admin'\n")
    for _ in range(4):
        policy.rules()
        clock.advance(3)
    assert len(messages(caplog, logging.WARNING)) == 1


def test_file_appearing_later_is_picked_up(pfile, clock):
    assert policy.rules() == policy.DEFAULT_RULES
    pfile.write("identity:domain:create: '@'\n")
    clock.advance(3)
    assert policy.rules()["identity:domain:create"] == "@"


def test_file_vanishing_means_defaults_again_silently(pfile, clock, caplog):
    pfile.write("identity:domain:create: '@'\n")
    assert policy.rules()["identity:domain:create"] == "@"
    pfile.remove()
    clock.advance(3)
    assert policy.rules() == policy.DEFAULT_RULES
    assert set(policy.sources().values()) == {"default"}
    assert no_logs(caplog)


def test_changing_the_env_path_loads_the_other_file(pfile, tmp_path, monkeypatch):
    pfile.write("identity:domain:create: '@'\n")
    assert policy.rules()["identity:domain:create"] == "@"
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(tmp_path / "other.yaml"))
    assert policy.rules() == policy.DEFAULT_RULES


def test_authorize_unknown_action_raises_key_error(pfile):
    with pytest.raises(KeyError):
        policy.authorize("identity:nonsense:do", ADMIN)


def test_authorize_unknown_action_raises_even_if_the_file_names_it(pfile):
    pfile.write("helper: 'role:admin'\n")
    with pytest.raises(KeyError):
        policy.authorize("helper", ADMIN)


def test_authorize_reads_token_claims(pfile):
    assert policy.authorize("identity:user:create", {"role": "admin"}) is True
    assert policy.authorize("identity:user:create", {"role": "member", "domain_admin": True}) is True
    assert policy.authorize("identity:user:create", {"role": "member", "domain_admin": False}) is False
    assert policy.authorize("identity:user:create", {"role": None, "domain_admin": None}) is False
    assert policy.authorize("identity:user:create", {}) is False


def test_reset_drops_the_cache(pfile):
    pfile.write("identity:domain:create: '@'\n")
    assert policy.rules()["identity:domain:create"] == "@"
    pfile.remove()
    policy.reset()
    assert policy.rules() == policy.DEFAULT_RULES


def test_module_imports_without_environment_setup():
    import subprocess

    code = (
        "import sys; sys.path.insert(0, %r); import shared.policy"
        % os.path.join(os.path.dirname(__file__), "..", "..", "services")
    )
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([sys.executable, "-I", "-c", code], env=env, check=True)


# Temporary wrappers; Task 5 removes them together with these tests.
def test_legacy_enforce_wrapper(pfile):
    from fastapi import HTTPException

    assert policy.enforce("identity:domain:create", ADMIN) is True
    assert policy.enforce("identity:domain:create", MEMBER, raise_on_deny=False) is False
    with pytest.raises(HTTPException) as exc:
        policy.enforce("identity:domain:create", MEMBER)
    assert exc.value.status_code == 403
    assert policy.enforce("some:unknown:action", MEMBER) is True
    assert policy.list_rules() == policy.rules()
