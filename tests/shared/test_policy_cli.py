import logging
import os
import re
from pathlib import Path

import pytest
import yaml
from shared import manage, policy

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "etc" / "cloudx" / "policy.yaml.sample"
ACTION_LINE = re.compile(r'^# (identity:[a-z-]+:[a-z-]+: ".*")$')


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(tmp_path / "policy.yaml"))
    policy.reset()
    yield
    policy.reset()


def run(capsys, *argv):
    status = manage.main(["policy", *argv])
    out = capsys.readouterr()
    return status, out.out.splitlines(), out.err


def write(tmp_path, text, name="policy.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_check_reports_each_problem(tmp_path, capsys):
    path = write(tmp_path, 'identity:user:create: "is_admin"\nidentity:user:lst: "role:admin"\n')
    status, lines, _ = run(capsys, "check", path)
    assert status == 1
    assert lines == [
        "policy file: rule for 'identity:user:create' ignored, the default applies: unknown atom 'is_admin'",
        "policy file: 'identity:user:lst' is not an action and no rule uses it (typo?)",
    ]


def test_check_reports_invalid_yaml_for_an_unquoted_at(tmp_path, capsys):
    # PyYAML decides: '@' is a reserved indicator and cannot start a plain scalar
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load("identity:user:list: @\n")
    path = write(tmp_path, "identity:user:list: @\n")
    status, lines, _ = run(capsys, "check", path)
    assert status == 1
    # the message is the engine's, and PyYAML's own text spans several lines
    assert lines[0].startswith(f"policy file {path} is not valid YAML: ")
    assert any("identity:user:list: @" in line for line in lines)  # PyYAML points at the offending line


def test_check_reports_a_top_level_that_is_not_a_mapping(tmp_path, capsys):
    path = write(tmp_path, "- identity:user:list\n")
    status, lines, _ = run(capsys, "check", path)
    assert status == 1
    assert lines == [f"policy file {path} must be a mapping of action to rule, not a list"]


def test_check_ok_exit_status_and_differing_list(tmp_path, capsys):
    path = write(tmp_path, 'identity:user:create: "role:member"\nidentity:user:list: "role:admin or domain_admin:True"\n')
    status, lines, _ = run(capsys, "check", path)
    assert status == 0
    # the second entry equals the default, so it is not an override
    assert lines == ['differs from the default: identity:user:create: role:member']


def test_check_exit_status_is_1_with_problems(tmp_path, capsys):
    path = write(tmp_path, 'identity:user:create: "role:member"\nidentity:domain:create: "role:"\n')
    status, lines, _ = run(capsys, "check", path)
    assert status == 1
    assert lines == [
        "policy file: rule for 'identity:domain:create' ignored, the default applies: missing name in 'role:'",
        "differs from the default: identity:user:create: role:member",
    ]


def test_check_a_rule_with_an_error_is_not_listed_as_differing(tmp_path, capsys):
    path = write(tmp_path, 'identity:user:create: "is_admin"\n')
    assert policy.differing(path) == []


def test_check_missing_file_is_ok(tmp_path, capsys):
    path = str(tmp_path / "nope.yaml")
    status, lines, _ = run(capsys, "check", path)
    assert status == 0
    assert lines == [f"{path}: not found, the defaults apply"]


@pytest.mark.parametrize("text", ["", "# only a comment\n", "---\n"])
def test_check_empty_file_has_no_overrides(tmp_path, capsys, text):
    path = write(tmp_path, text)
    status, lines, _ = run(capsys, "check", path)
    assert status == 0
    assert lines == [f"{path}: no overrides"]


def test_check_without_argument_uses_the_configured_file(tmp_path, capsys):
    write(tmp_path, 'identity:user:create: "!"\n')
    status, lines, _ = run(capsys, "check")
    assert status == 0
    assert lines == ["differs from the default: identity:user:create: !"]


def test_check_an_unreadable_file_is_a_problem(tmp_path, capsys):
    path = tmp_path / "policy.yaml"
    path.write_bytes(b"\xff\xfe\x00bad")
    status, lines, _ = run(capsys, "check", str(path))
    assert status == 1
    assert len(lines) == 1 and lines[0].startswith(f"policy file {path} cannot be read: ")


def test_check_messages_are_the_ones_the_engine_logs(tmp_path, caplog):
    text = ('identity:user:create: "is_admin"\nidentity:user:lst: "role:admin"\n'
            'a: "rule:b"\nb: "rule:a"\nidentity:user:list: 5\n')
    path = write(tmp_path, text)
    found = policy.check_file(path)
    caplog.set_level(logging.DEBUG, logger="cloudx-policy")
    policy.reset()
    policy.rules()
    logged = [r.getMessage() for r in caplog.records if r.name == "cloudx-policy"]
    assert len(found) == 5
    assert found == logged


@pytest.mark.parametrize("text", ["identity:user:list: @\n", "- x\n", 'identity:user:create: "role:member"\n'])
def test_check_file_does_not_touch_the_engine_cache_or_log(tmp_path, monkeypatch, caplog, text):
    good = write(tmp_path, 'identity:user:create: "role:member"\n', name="good.yaml")
    monkeypatch.setenv("CLOUDX_POLICY_FILE", good)
    policy.reset()
    before = policy.sources()
    cache = policy._cache
    caplog.set_level(logging.DEBUG, logger="cloudx-policy")
    caplog.clear()
    policy.check_file(write(tmp_path, text, name="other.yaml"))
    policy.differing(write(tmp_path, text, name="other.yaml"))
    assert policy._cache is cache
    assert policy.sources() == before
    assert [r for r in caplog.records if r.name == "cloudx-policy"] == []


def test_defaults_prints_every_action_commented(capsys):
    status, lines, _ = run(capsys, "defaults")
    assert status == 0
    commented = [line for line in lines if ACTION_LINE.match(line)]
    assert commented == [f'# {action}: "{rule}"' for action, rule in policy.DEFAULT_RULES.items()]
    assert len(commented) == 22


def test_defaults_output_is_policy_defaults_yaml_without_an_extra_newline(capsys):
    manage.main(["policy", "defaults"])
    assert capsys.readouterr().out == policy.defaults_yaml()
    assert policy.defaults_yaml().endswith("\n") and not policy.defaults_yaml().endswith("\n\n")
    assert policy.defaults_yaml() == policy.defaults_yaml()


def test_header_documents_the_format():
    header = policy.defaults_yaml().split("# identity:", 1)[0]
    for needle in ("/etc/cloudx/policy.yaml", "640", "cloudx", '"!"', '"@"', "role:NAME", "domain_admin:True",
                   "rule:NAME", "and", "or", "not", "every node", "cloudx-manage policy check"):
        assert needle in header, needle


def test_uncommenting_the_defaults_gives_exactly_the_table():
    uncommented = "\n".join(
        m.group(1) if (m := ACTION_LINE.match(line)) else line for line in policy.defaults_yaml().splitlines())
    assert yaml.safe_load(uncommented) == policy.DEFAULT_RULES


def test_sample_file_equals_defaults_output():
    assert SAMPLE.read_text(encoding="utf-8") == policy.defaults_yaml()


def test_the_sample_as_installed_is_a_no_op(tmp_path, monkeypatch, caplog):
    text = SAMPLE.read_text(encoding="utf-8")
    assert all(not line.strip() or line.lstrip().startswith("#") for line in text.splitlines())
    assert policy.check_file(str(SAMPLE)) == []
    assert policy.differing(str(SAMPLE)) == []
    monkeypatch.setenv("CLOUDX_POLICY_FILE", str(SAMPLE))
    policy.reset()
    caplog.set_level(logging.DEBUG, logger="cloudx-policy")
    assert policy.rules() == policy.DEFAULT_RULES
    assert set(policy.sources().values()) == {"default"}
    assert [r for r in caplog.records if r.name == "cloudx-policy" and r.levelno >= logging.WARNING] == []


def test_other_subcommands_still_parse_without_the_auth_secret(monkeypatch):
    monkeypatch.delenv("AUTH_SECRET_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        manage.main(["policy"])
    assert exc.value.code == 2
