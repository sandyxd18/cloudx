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


def _is_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
        isinstance(node, ast.Name) and node.id == "environ"
    )


def _env_name(node: ast.AST) -> str | None:
    """The literal name when `node` reads the environment, whatever the module is called (`os`, `_os`, `from os import ...`)."""
    if isinstance(node, ast.Call) and node.args:
        func = node.func
        is_getenv = (isinstance(func, ast.Attribute) and func.attr == "getenv") or (
            isinstance(func, ast.Name) and func.id == "getenv"
        )
        is_environ_get = isinstance(func, ast.Attribute) and func.attr == "get" and _is_environ(func.value)
        first = node.args[0]
        if (is_getenv or is_environ_get) and isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    if isinstance(node, ast.Subscript) and _is_environ(node.value):
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


def test_the_scan_covers_the_services_tree():
    assert len(list(_sources())) > 100  # a wrong path or filter must not let the guard pass by scanning nothing


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
        "import os as _os\n"
        "from os import environ, getenv\n"
        "A = os.getenv('IMAGE_API_URL', 'http://x')\n"
        "B = os.environ['BLOCK_STORE_API_URL']\n"
        "C = os.environ.get('DNS_API_URL')\n"
        "D = environ.get('PLACEMENT_API_URL', '')\n"
        "E = _os.getenv('IMAGE_API_URL')\n"
        "F = getenv('NETWORKING_API_URL')\n"
        "G = _os.environ['COMPUTE_API_URL']\n",
        encoding="utf-8",
    )
    assert direct_reads(bad) == [
        (4, "IMAGE_API_URL"),
        (5, "BLOCK_STORE_API_URL"),
        (6, "DNS_API_URL"),
        (7, "PLACEMENT_API_URL"),
        (8, "IMAGE_API_URL"),
        (9, "NETWORKING_API_URL"),
        (10, "COMPUTE_API_URL"),
    ]
    ok = tmp_path / "ok.py"
    ok.write_text(
        '"""Reads IMAGE_API_URL through the catalog."""\n'
        "import os\n"
        "import os as _os\n"
        "# os.getenv('IMAGE_API_URL')\n"
        "AUTH = os.getenv('AUTH_API_URL', 'http://auth')\n"
        "AUTH2 = _os.environ.get('AUTH_API_URL')\n"
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
