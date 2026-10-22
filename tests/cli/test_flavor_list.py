import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner  # noqa: E402

from cloudx_cli.commands import compute  # noqa: E402
from cloudx_cli.main import cli  # noqa: E402


def test_flavor_list_shows_the_memory_and_disk_the_api_returns(monkeypatch):
    # the API names these fields ram_mb and disk_gb; the table asked for ram and disk and printed blanks
    flavors = [{"id": "f1", "name": "big", "vcpus": 4, "ram_mb": 7777, "disk_gb": 123}]
    monkeypatch.setattr(compute, "_api", lambda method, url, profile="default", **kw: {"items": flavors})

    res = CliRunner().invoke(cli, ["flavor", "list"])

    assert res.exit_code == 0, res.output
    assert "7777" in res.output and "123" in res.output
