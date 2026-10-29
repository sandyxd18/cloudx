import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from cloudx_cli import common  # noqa: E402
from cloudx_cli.commands import auth as auth_cmd  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402

LAB = "http://172.20.0.151:8005"


@pytest.fixture
def home(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("CLOUDX_"):
            monkeypatch.delenv(k)
    for module in (common, auth_cmd):
        monkeypatch.setattr(module, "CLOUDX_DIR", tmp_path, raising=False)
        monkeypatch.setattr(module, "CONFIG_FILE", tmp_path / "config")
        monkeypatch.setattr(module, "CREDS_FILE", tmp_path / "credentials")
    monkeypatch.setattr(common, "DEFAULT_URLS", common._LazyUrls())
    common._reset_catalog()
    yield tmp_path
    common._reset_catalog()


def test_auth_url_saved_by_login_is_used_by_later_commands(home):
    # `auth login` wrote auth_url to [default] and nothing read it back: every later command went to 127.0.0.1:8005
    (home / "config").write_text(f"[default]\nauth_url = {LAB}\nproject = admin\noutput = json\n")
    assert common.DEFAULT_URLS["auth"] == LAB
    assert common._load_config("default")["output"] == "json"


def test_env_beats_the_saved_url(home, monkeypatch):
    (home / "config").write_text(f"[default]\nauth_url = {LAB}\n")
    monkeypatch.setenv("CLOUDX_AUTH_URL", "http://other:8005")
    assert common.DEFAULT_URLS["auth"] == "http://other:8005"


def test_without_env_or_config_the_default_is_local(home):
    assert common.DEFAULT_URLS["auth"] == "http://127.0.0.1:8005"
    (home / "config").write_text("[default]\nproject = admin\n")
    assert common.DEFAULT_URLS["auth"] == "http://127.0.0.1:8005"


def test_a_named_profile_reads_its_own_section(home):
    (home / "config").write_text(f"[default]\nauth_url = {LAB}\n[profile edge]\nauth_url = http://edge:8005\n")
    common.set_profile("edge")
    try:
        assert common.DEFAULT_URLS["auth"] == "http://edge:8005"
        assert common._load_config("edge")["auth_url"] == "http://edge:8005"
    finally:
        common.set_profile("default")
    assert common.DEFAULT_URLS["auth"] == LAB


def test_both_spellings_of_the_default_section_are_read(home):
    # `auth configure` writes [DEFAULT]
    (home / "config").write_text("[DEFAULT]\nproject = demo\noutput = json\n")
    assert common._load_config("default") == {"project": "demo", "output": "json"}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_login_then_the_next_command_finds_the_auth_api(home, monkeypatch):
    calls = []

    def post(url, **kw):
        calls.append((url, kw.get("headers")))
        if url.endswith("/auth/tokens"):
            return Resp(201, {"token": "t", "catalog": []})
        if url.endswith("/access-keys"):
            return Resp(201, {"access_key_id": "ak", "secret_access_key": "sk"})
        return Resp(201, {"token": "t2"})

    monkeypatch.setattr(common.requests, "post", post)
    out = CliRunner().invoke(cli, ["auth", "login", "--username", "admin", "--password", "pw", "--auth-url", LAB])
    assert out.exit_code == 0, out.output
    assert calls[0][0] == f"{LAB}/auth/tokens"

    # a new process: no environment, no login catalog, only the two files login wrote
    calls.clear()
    common._reset_catalog()
    assert common._get_token("default") == "t2"
    assert calls == [(f"{LAB}/auth/tokens/access-key", {"X-Access-Key-Id": "ak", "X-Secret-Access-Key": "sk"})]
