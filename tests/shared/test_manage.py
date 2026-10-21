import os
import re
import stat

from shared import manage, migrate

SECRET_KEYS = ["auth_secret_key", "auth_admin_password", "key_manager_master_key", "metrics_token", "internal_token"]

SAMPLE = """# header comment
[DEFAULT]
auth_secret_key =
auth_admin_password =
key_manager_master_key =
metrics_token =
internal_token =

database_url = postgresql://x/y
# auth_secret_key = commented-out
"""


def values(path):
    out = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^(\w+) *= *(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def make_sample(tmp_path):
    sample = tmp_path / "cloudx.conf.sample"
    sample.write_text(SAMPLE)
    return sample


def test_db_sync_delegates_to_migrate(monkeypatch):
    calls = []
    monkeypatch.setattr(migrate, "main", lambda argv=None: calls.append(argv))
    assert manage.main(["db", "sync", "compute"]) == 0
    assert calls == [["compute"]]


def test_db_sync_without_services_migrates_all(monkeypatch):
    calls = []
    monkeypatch.setattr(migrate, "main", lambda argv=None: calls.append(argv))
    assert manage.main(["db", "sync"]) == 0
    assert calls == [[]]


def test_conf_init_creates_missing_conf_with_generated_secrets(tmp_path):
    conf = tmp_path / "etc" / "cloudx.conf"
    conf.parent.mkdir()
    generated = manage.init_conf(conf, make_sample(tmp_path))
    assert sorted(generated) == sorted(SECRET_KEYS)
    assert mode(conf) == 0o640
    vals = values(conf)
    assert all(vals[k] for k in SECRET_KEYS)
    assert re.fullmatch(r"[0-9a-f]{64}", vals["auth_secret_key"])
    assert re.fullmatch(r"[0-9a-f]{64}", vals["key_manager_master_key"])
    assert re.fullmatch(r"[0-9a-f]{48}", vals["metrics_token"])
    assert re.fullmatch(r"[0-9a-f]{48}", vals["internal_token"])
    assert re.fullmatch(r"[A-Za-z0-9]{18}", vals["auth_admin_password"])
    assert vals["database_url"] == "postgresql://x/y"
    assert [p.name for p in conf.parent.iterdir()] == ["cloudx.conf"]


def test_conf_init_never_regenerates_existing_secrets(tmp_path):
    conf = tmp_path / "cloudx.conf"
    sample = make_sample(tmp_path)
    manage.init_conf(conf, sample)
    first = conf.read_text()
    before = values(conf)
    assert manage.init_conf(conf, sample) == []
    assert conf.read_text() == first

    conf.write_text(first.replace(f"internal_token = {before['internal_token']}", "internal_token ="))
    assert manage.init_conf(conf, sample) == ["internal_token"]
    after = values(conf)
    assert after["internal_token"] and after["internal_token"] != before["internal_token"]
    assert {k: v for k, v in after.items() if k != "internal_token"} == {k: v for k, v in before.items() if k != "internal_token"}


def test_conf_init_keeps_existing_values_comments_and_order(tmp_path):
    conf = tmp_path / "cloudx.conf"
    conf.write_text("# mine\n[DEFAULT]\nauth_secret_key = keep-me\nfoo = bar\n# auth_admin_password = nope\nmetrics_token =   \n")
    conf.chmod(0o600)
    generated = manage.init_conf(conf, make_sample(tmp_path))
    assert sorted(generated) == ["auth_admin_password", "internal_token", "key_manager_master_key", "metrics_token"]
    lines = conf.read_text().splitlines()
    assert lines[:2] == ["# mine", "[DEFAULT]"]
    assert lines.index("auth_secret_key = keep-me") < lines.index("foo = bar") < lines.index("# auth_admin_password = nope")
    assert "metrics_token =   " not in lines
    vals = values(conf)
    assert all(vals[k] for k in SECRET_KEYS)
    assert vals["auth_secret_key"] == "keep-me" and vals["foo"] == "bar"
    assert mode(conf) == 0o640


def test_conf_init_never_exposes_secrets_before_chmod(tmp_path, monkeypatch):
    conf = tmp_path / "cloudx.conf"
    modes = []
    real_replace = os.replace

    def spy(src, dst):
        modes.append(mode(src))
        return real_replace(src, dst)

    monkeypatch.setattr(manage.os, "replace", spy)
    manage.init_conf(conf, make_sample(tmp_path))
    assert modes == [0o640]


def test_conf_init_cli(tmp_path):
    conf = tmp_path / "cloudx.conf"
    sample = make_sample(tmp_path)
    assert manage.main(["conf", "init", "--path", str(conf), "--sample", str(sample)]) == 0
    assert all(values(conf)[k] for k in SECRET_KEYS)
