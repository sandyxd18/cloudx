"""token_matches compares any two str values as UTF-8 bytes and never raises."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from shared.internal import token_matches  # noqa: E402


def test_equal_ascii_matches():
    assert token_matches("tok", "tok") is True


def test_unequal_does_not_match():
    assert token_matches("tok", "tok2") is False
    assert token_matches("", "tok") is False


def test_equal_non_ascii_matches():
    assert token_matches("café", "café") is True


def test_non_ascii_against_ascii_does_not_match():
    assert token_matches("café", "cafe") is False
    assert token_matches("cafe", "café") is False


def test_empty_expected_never_matches():
    assert token_matches("", "") is False
    assert token_matches("anything", "") is False


def test_very_long_values():
    big = "x" * 100_000
    assert token_matches(big, big) is True
    assert token_matches(big, big + "y") is False


@pytest.mark.parametrize("value", ["é", "\udcff", "日本語", "a\x00b", "\U0001f600"])
def test_never_raises(value):
    assert token_matches(value, "tok") is False
    assert token_matches("tok", value) is False
    assert token_matches(value, value) is True
