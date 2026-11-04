import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import auth  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def _capture(monkeypatch):
    calls = []

    def fake_api(method, url, profile="default", **kw):
        calls.append((method, url, kw.get("json")))
        return {"id": "c1", "name": "ci", "secret": "s3cr3t-value"}

    monkeypatch.setattr(auth, "_api", fake_api)
    monkeypatch.setattr(auth, "DEFAULT_URLS", {"auth": "http://auth.test:8000"})
    return calls


def test_rotate_prints_the_new_secret_once(monkeypatch):
    calls = _capture(monkeypatch)

    res = CliRunner().invoke(cli, ["auth", "appcred", "rotate", "alice", "ci"])

    assert res.exit_code == 0, res.output
    assert calls == [("post", "http://auth.test:8000/v3/users/alice/application_credentials/ci/rotate", {"grace_seconds": 0})]
    assert res.output.count("s3cr3t-value") == 1
    assert "not be shown again" in res.output


def test_rotate_passes_the_grace_period(monkeypatch):
    calls = _capture(monkeypatch)

    res = CliRunner().invoke(cli, ["auth", "appcred", "rotate", "alice", "ci", "--grace-seconds", "300"])

    assert res.exit_code == 0, res.output
    assert calls[0][2] == {"grace_seconds": 300}
    assert "300" in res.output
