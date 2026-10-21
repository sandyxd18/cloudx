"""tests/conftest.py resets the catalog client (cache and retry backoff) around every test.

The two tests rely on running in this order: the first dirties the module state without cleaning up,
the second must find it clean.
"""
import sys
import time

from shared import catalog


def _loaded():
    return [m for name in ("shared.catalog", "services.shared.catalog") if (m := sys.modules.get(name))]


def test_a_dirties_the_catalog_state_and_does_not_clean_up():
    for module in _loaded():
        module._cache = (time.monotonic(), [{"name": "image", "endpoints": []}])
        module._retry = (time.monotonic() + 3600, RuntimeError("auth down"))
    assert catalog._cache is not None and catalog._retry is not None


def test_b_finds_the_catalog_state_clean():
    for module in _loaded():
        assert module._cache is None
        assert module._retry is None
