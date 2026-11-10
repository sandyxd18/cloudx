"""A key or a bucket with a URL character in it (`#`, `?`, `%`, a space) must reach object-store as written: the CLI put it
into the URL unquoted, so `put --key 'a b#c.txt'` stored the key `a b`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from click.testing import CliRunner
from cloudx_cli.commands import object_store as osc
from cloudx_cli.main import cli

BASE = osc.DEFAULT_URLS["object-store"]
KEY = "dir/a b#c?d%e.txt"
QUOTED = "dir/a%20b%23c%3Fd%25e.txt"


class _Resp:
    ok = True
    status_code = 200
    content = b"data"

    def json(self):
        return {"key": "stored"}


def invoke(monkeypatch, args, tmp_path=None):
    seen = {}

    def fake_http(method):
        def call(url, **kw):
            seen["method"], seen["url"], seen["files"] = method, url, kw.get("files")
            return _Resp()
        return call

    def fake_api(method, url, profile="default", **kw):
        seen["method"], seen["url"], seen["kw"] = method, url, kw
        return {"objects": []}

    monkeypatch.setattr(osc.requests, "put", fake_http("put"))
    monkeypatch.setattr(osc.requests, "get", fake_http("get"))
    monkeypatch.setattr(osc, "_api", fake_api)
    monkeypatch.setattr(osc, "_headers", lambda profile="default": {"Content-Type": "application/json"})
    res = CliRunner().invoke(cli, ["-o", "json", "object-store", *args])
    assert res.exit_code == 0, res.output
    return seen


def test_put_quotes_the_key_in_the_url_and_sends_it_as_the_file_name(monkeypatch, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    seen = invoke(monkeypatch, ["object", "put", "bkt", str(f), "--key", KEY])
    assert seen["url"] == f"{BASE}/buckets/bkt/objects/{QUOTED}"
    assert seen["files"]["file"][0] == KEY


def test_get_quotes_the_key(monkeypatch, tmp_path):
    seen = invoke(monkeypatch, ["object", "get", "bkt", KEY, "--out", str(tmp_path / "o")])
    assert seen["url"] == f"{BASE}/buckets/bkt/objects/{QUOTED}"


def test_delete_quotes_the_key(monkeypatch):
    seen = invoke(monkeypatch, ["object", "delete", "bkt", KEY])
    assert (seen["method"], seen["url"]) == ("delete", f"{BASE}/buckets/bkt/objects/{QUOTED}")


def test_a_bucket_name_is_one_path_segment(monkeypatch):
    seen = invoke(monkeypatch, ["object", "delete", "b/k#t", "k"])
    assert seen["url"] == f"{BASE}/buckets/b%2Fk%23t/objects/k"


def test_list_prefix_is_sent_as_a_query_parameter(monkeypatch):
    seen = invoke(monkeypatch, ["object", "list", "bkt", "--prefix", "a b&c=d#e"])
    assert seen["url"] == f"{BASE}/buckets/bkt/objects"
    assert seen["kw"]["params"] == {"prefix": "a b&c=d#e"}
