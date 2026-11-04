"""The whole policy table is used: every action is a route gate somewhere, or one of the two inline sub-actions."""
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared import policy  # noqa: E402
from tests.stack import fresh_service  # noqa: E402

# decided inside POST /volumes/{ref}/action by policy.check(), so no route dependency carries them
INLINE_ACTIONS = {"block-store:volume:force-delete", "block-store:volume:reset-status"}

# services whose app is one main.py; they are imported anew so that every require() runs again
SINGLE_MODULE_SERVICES = ("placement", "billing", "telemetry", "object_store", "ha")


def _import_every_service(monkeypatch, tmp_path):
    # start from an empty registry (put back afterwards): require() calls made by other tests must not count
    monkeypatch.setattr(policy, "_required", set())
    monkeypatch.setenv("ACCEPT_DOMAIN_SCOPE", "true")  # billing sets it at import; this way it is undone afterwards
    monkeypatch.setenv("VOLUME_STORE_PATH", str(tmp_path / "volumes"))
    monkeypatch.setenv("REVOCATION_CHECK", "false")
    monkeypatch.setenv("INTERNAL_TOKEN", "service-token")
    for name in ("auth", "compute", "block_store", "networking"):
        fresh_service(name, monkeypatch, tmp_path)
    for name in SINGLE_MODULE_SERVICES:
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / (name + '.sqlite')}")
        monkeypatch.setenv("BILLING_DATABASE_URL", f"sqlite:///{tmp_path / 'billing.sqlite'}")
        module = f"services.{name}.app.main"
        sys.modules.pop(module, None)
        importlib.import_module(module)


def test_every_action_is_used_by_a_route_or_is_a_known_inline_action(monkeypatch, tmp_path):
    _import_every_service(monkeypatch, tmp_path)

    used = policy.required_actions() | INLINE_ACTIONS

    assert used == set(policy.DEFAULT_RULES), (
        f"in the table but gated nowhere: {sorted(set(policy.DEFAULT_RULES) - used)}; "
        f"gated but not in the table: {sorted(used - set(policy.DEFAULT_RULES))}"
    )
    assert len(policy.DEFAULT_RULES) == 57
