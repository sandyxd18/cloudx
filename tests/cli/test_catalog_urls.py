import os
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from cloudx_cli import common  # noqa: E402

CATALOG = [
    {"name": "identity", "type": "identity",
     "endpoints": [{"interface": "public", "url": "http://infra01:8005"}]},
    {"name": "image", "type": "image",
     "endpoints": [{"interface": "internal", "url": "http://10.0.0.5:8002"},
                   {"interface": "public", "url": "http://infra01:8002"}]},
    {"name": "volume", "type": "volume",
     "endpoints": [{"interface": "public", "url": "http://infra01:8004"}]},
    {"name": "network", "type": "network",
     "endpoints": [{"interface": "public", "url": "http://infra01:8001"}]},
    {"name": "file-store", "type": "file-store",
     "endpoints": [{"interface": "public", "url": "http://infra01:8017"}]},
]


class Resp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad_json = bad_json
        self.ok = status < 400

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


class FakeNet:
    def __init__(self, monkeypatch):
        self.posts = []
        self.gets = []
        self.login = Resp(201, {"token": "tok-login", "catalog": CATALOG})
        self.catalog = Resp(200, {"catalog": CATALOG})
        self.error = None
        monkeypatch.setattr(common.requests, "post", self._post)
        monkeypatch.setattr(common.requests, "get", self._get)

    def _post(self, url, **kw):
        self.posts.append((url, kw))
        if self.error:
            raise self.error
        return self.login

    def _get(self, url, **kw):
        self.gets.append((url, kw))
        if self.error:
            raise self.error
        return self.catalog

    @property
    def calls(self):
        return len(self.posts) + len(self.gets)


@pytest.fixture
def net(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("CLOUDX_"):
            monkeypatch.delenv(k)
    monkeypatch.setattr(common, "CREDS_FILE", tmp_path / "credentials")
    monkeypatch.setattr(common, "CONFIG_FILE", tmp_path / "config")  # not the developer's own ~/.cloudx/config
    monkeypatch.setattr(common, "DEFAULT_URLS", common._LazyUrls())
    common._reset_catalog()
    yield FakeNet(monkeypatch)
    common._reset_catalog()


def login_env(monkeypatch):
    monkeypatch.setenv("CLOUDX_USERNAME", "admin")
    monkeypatch.setenv("CLOUDX_PASSWORD", "pw")


def test_catalog_from_login_used_when_env_unset(net, monkeypatch):
    login_env(monkeypatch)
    assert common.DEFAULT_URLS["image"] == "http://infra01:8002"
    assert net.posts[0][0].endswith("/auth/tokens")
    assert net.gets == []


def test_env_beats_catalog_without_network(net, monkeypatch):
    login_env(monkeypatch)
    monkeypatch.setenv("CLOUDX_IMAGE_URL", "http://override:1")
    urls = common._LazyUrls()
    assert urls["image"] == "http://override:1"
    assert urls.get("image") == "http://override:1"
    assert net.calls == 0


def test_token_env_only_fetches_catalog_route(net, monkeypatch):
    monkeypatch.setenv("CLOUDX_TOKEN", "tok-env")
    assert common.DEFAULT_URLS["volume"] == "http://infra01:8004"
    assert net.posts == []
    url, kw = net.gets[0]
    assert url == "http://127.0.0.1:8005/v3/auth/catalog"
    assert kw["headers"] == {"X-Auth-Token": "tok-env"}
    assert kw["timeout"] == 5


def test_access_key_flow_falls_back_to_catalog_route(net, monkeypatch):
    monkeypatch.setenv("CLOUDX_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("CLOUDX_SECRET_ACCESS_KEY", "s")
    net.login = Resp(201, {"token": "tok-ak"})
    assert common.DEFAULT_URLS["network"] == "http://infra01:8001"
    assert net.gets[0][1]["headers"] == {"X-Auth-Token": "tok-ak"}


@pytest.mark.parametrize("failure", ["404", "conn", "badjson", "nocatalogkey", "notlist"])
def test_unreachable_or_old_auth_falls_back_quietly(net, monkeypatch, capsys, failure):
    monkeypatch.setenv("CLOUDX_TOKEN", "tok-env")
    if failure == "404":
        net.catalog = Resp(404, {"detail": "Not Found"})
    elif failure == "conn":
        net.error = requests.ConnectionError("down")
    elif failure == "badjson":
        net.catalog = Resp(200, bad_json=True)
    elif failure == "nocatalogkey":
        net.catalog = Resp(200, {"x": 1})
    else:
        net.catalog = Resp(200, {"catalog": "oops"})
    assert common.DEFAULT_URLS["image"] == "http://127.0.0.1:8002"
    assert common.DEFAULT_URLS["file_store"] == "http://127.0.0.1:8017"
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    assert len(net.gets) == 1


@pytest.mark.parametrize("failure", ["http", "conn"])
def test_login_failure_exits_quietly_to_defaults(net, monkeypatch, capsys, failure):
    login_env(monkeypatch)
    if failure == "http":
        net.login = Resp(401, {"detail": "no"})
    else:
        net.error = requests.ConnectionError("down")
    assert common.DEFAULT_URLS["image"] == "http://127.0.0.1:8002"
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_no_credentials_means_defaults_and_no_network(net, capsys):
    assert common.DEFAULT_URLS["image"] == "http://127.0.0.1:8002"
    assert net.calls == 0
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_lookups_are_memoized(net, monkeypatch):
    login_env(monkeypatch)
    for _ in range(5):
        common.DEFAULT_URLS["image"]
        common.DEFAULT_URLS["volume"]
        common.DEFAULT_URLS.get("network")
    assert len(net.posts) == 1
    assert len(net.gets) == 0


def test_failure_is_memoized_too(net, monkeypatch):
    monkeypatch.setenv("CLOUDX_TOKEN", "tok-env")
    net.error = requests.ConnectionError("down")
    for _ in range(5):
        common.DEFAULT_URLS["image"]
    assert len(net.gets) == 1


def test_auth_never_touches_network(net, monkeypatch):
    login_env(monkeypatch)
    assert common.DEFAULT_URLS["auth"] == "http://127.0.0.1:8005"
    monkeypatch.setenv("CLOUDX_AUTH_URL", "http://auth.example:8005")
    assert common._LazyUrls()["auth"] == "http://auth.example:8005"
    assert net.calls == 0


def test_missing_service_uses_default(net, monkeypatch):
    login_env(monkeypatch)
    assert common.DEFAULT_URLS["dns"] == "http://127.0.0.1:8016"


def test_cli_keys_map_to_catalog_names(net, monkeypatch):
    login_env(monkeypatch)
    assert common.DEFAULT_URLS["volume"] == "http://infra01:8004"
    assert common.DEFAULT_URLS["network"] == "http://infra01:8001"
    assert common.DEFAULT_URLS["file_store"] == "http://infra01:8017"


def test_public_endpoint_wins_over_internal(net, monkeypatch):
    login_env(monkeypatch)
    assert common.DEFAULT_URLS["image"] == "http://infra01:8002"


def test_internal_only_service_falls_back_to_default(net, monkeypatch):
    login_env(monkeypatch)
    net.login = Resp(201, {"token": "t", "catalog": [
        {"name": "image", "endpoints": [{"interface": "internal", "url": "http://10.0.0.5:8002"}]}]})
    assert common.DEFAULT_URLS["image"] == "http://127.0.0.1:8002"


def test_dict_behaviour_kept(net):
    urls = common.DEFAULT_URLS
    assert len(urls) == 21
    assert "image" in urls and "nope" not in urls
    assert urls.get("nope") is None
    assert urls.get("nope", "x") == "x"
    with pytest.raises(KeyError):
        urls["nope"]


def test_login_inside_get_token_keeps_return_value_and_feeds_catalog(net, monkeypatch):
    login_env(monkeypatch)
    assert common._get_token() == "tok-login"
    assert common.DEFAULT_URLS["image"] == "http://infra01:8002"
    assert len(net.posts) == 1


def test_lookup_uses_profile_set_by_the_cli(net, monkeypatch):
    seen = []
    monkeypatch.setattr(common, "_get_token", lambda profile="default": seen.append(profile))
    common.set_profile("lab")
    common.DEFAULT_URLS["image"]
    assert seen == ["lab"]
