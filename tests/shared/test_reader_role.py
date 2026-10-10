import pytest
from fastapi import HTTPException
from starlette.requests import Request

from services.shared.auth_middleware import check_reader


def req(method, path="/servers"):
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def test_reader_reads_but_cannot_write():
    reader = {"role": "reader"}
    assert check_reader(reader, req("GET")) is reader
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(HTTPException) as e:
            check_reader(reader, req(method))
        assert e.value.status_code == 403
    check_reader(reader, req("DELETE", "/auth/tokens/current"))  # logout is allowed


def test_other_roles_unaffected():
    for role in ("admin", "member"):
        check_reader({"role": role}, req("DELETE"))
