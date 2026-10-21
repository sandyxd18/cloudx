"""Service URLs are resolved through shared.catalog; only auth_api_url is read straight from the environment."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
SAMPLE = ROOT / "etc" / "cloudx" / "cloudx.conf.sample"

# Files that may read *_API_URL directly. A new exception has to be added here on purpose.
ALLOWED = {
    "services/shared/catalog.py",
    "services/shared/services.py",
    "services/auth/app/config.py",  # the auth bootstrap builds its own table from env
}

_URL_ENV = re.compile(r"^[A-Z0-9_]+_API_URL$")
_SKIPPED_DIRS = {"tests", "migrations", "node_modules", "__pycache__"}


def _env_name(node: ast.AST) -> str | None:
    """The literal name when `node` reads the environment: os.getenv/os.environ.get/environ.get(...) or os.environ[...]."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
        func = node.func
        owner = func.value
        is_getenv = func.attr == "getenv" and isinstance(owner, ast.Name) and owner.id == "os"
        is_environ_get = func.attr == "get" and (
            (isinstance(owner, ast.Attribute) and owner.attr == "environ")
            or (isinstance(owner, ast.Name) and owner.id == "environ")
        )
        first = node.args[0]
        if (is_getenv or is_environ_get) and isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    if isinstance(node, ast.Subscript):
        owner = node.value
        if (isinstance(owner, ast.Attribute) and owner.attr == "environ") or (
            isinstance(owner, ast.Name) and owner.id == "environ"
        ):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return key.value
    return None


def direct_reads(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        name = _env_name(node)
        if name and _URL_ENV.match(name) and name != "AUTH_API_URL":
            found.append((node.lineno, name))
    return sorted(found)


def _sources():
    for path in sorted(SERVICES.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if _SKIPPED_DIRS & set(rel.parts) or rel.as_posix() in ALLOWED:
            continue
        yield path


def test_only_the_catalog_reads_service_urls_from_the_environment():
    offenders = [
        f"{path.relative_to(ROOT).as_posix()}:{line}: {name}"
        for path in _sources()
        for line, name in direct_reads(path)
    ]
    assert not offenders, "resolve these through shared.catalog.url(...):\n" + "\n".join(offenders)


def test_the_allowlist_is_explicit_and_points_at_real_files():
    assert ALLOWED == {
        "services/shared/catalog.py",
        "services/shared/services.py",
        "services/auth/app/config.py",
    }
    for rel in ALLOWED:
        assert (ROOT / rel).is_file(), rel


def test_scanner_reports_a_direct_read_and_ignores_auth_and_text(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import os\n"
        "from os import environ\n"
        "A = os.getenv('IMAGE_API_URL', 'http://x')\n"
        "B = os.environ['BLOCK_STORE_API_URL']\n"
        "C = os.environ.get('DNS_API_URL')\n"
        "D = environ.get('PLACEMENT_API_URL', '')\n",
        encoding="utf-8",
    )
    assert direct_reads(bad) == [
        (3, "IMAGE_API_URL"),
        (4, "BLOCK_STORE_API_URL"),
        (5, "DNS_API_URL"),
        (6, "PLACEMENT_API_URL"),
    ]
    ok = tmp_path / "ok.py"
    ok.write_text(
        '"""Reads IMAGE_API_URL through the catalog."""\n'
        "import os\n"
        "# os.getenv('IMAGE_API_URL')\n"
        "AUTH = os.getenv('AUTH_API_URL', 'http://auth')\n"
        "PEERS = {'IMAGE_API_URL': 'image'}\n"
        "def endpoint(args, key, env_name):\n"
        "    return env_name\n"
        "X = endpoint(None, 'k', 'IMAGE_API_URL')\n"
        "print('IMAGE_API_URL is unset')\n",
        encoding="utf-8",
    )
    assert direct_reads(ok) == []


def _active_keys(text: str) -> list[str]:
    keys = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip().lower())
    return keys


def test_shipped_conf_keeps_only_auth_api_url_among_the_api_urls():
    active = [k for k in _active_keys(SAMPLE.read_text(encoding="utf-8")) if k.endswith("_api_url")]
    assert active == ["auth_api_url"]


def test_conf_scanner_ignores_comments():
    assert _active_keys("[DEFAULT]\n# image_api_url = http://x\nauth_api_url = http://a\n  # compute_api_url = y\n") == [
        "auth_api_url"
    ]
