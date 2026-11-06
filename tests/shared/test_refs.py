"""path_ref quotes a reference for use as one URL path segment and refuses the dot segments."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from fastapi import HTTPException  # noqa: E402

from shared.refs import path_ref  # noqa: E402


def test_plain_reference_is_unchanged():
    assert path_ref("web-1") == "web-1"
    assert path_ref("3f2a-9c") == "3f2a-9c"


def test_spaces_and_slashes_are_quoted():
    assert path_ref("my server") == "my%20server"
    assert path_ref("a/b") == "a%2Fb"
    assert path_ref("a?b#c") == "a%3Fb%23c"


@pytest.mark.parametrize("ref", [".", ".."])
def test_dot_segments_are_not_found(ref):
    with pytest.raises(HTTPException) as exc:
        path_ref(ref)
    assert exc.value.status_code == 404
    assert exc.value.detail == f"'{ref}' not found"


@pytest.mark.parametrize("ref", ["...", "a.b", ".a", "a.", "v1.2"])
def test_other_dotted_references_pass(ref):
    assert path_ref(ref) == ref


def test_encoded_dots_are_quoted_again():
    assert path_ref("%2e%2e") == "%252e%252e"
