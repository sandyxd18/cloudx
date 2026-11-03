"""A stack update that changes a server's flavor is a decision to resize, so the engine confirms the resize when the
server reaches VERIFY_RESIZE; left there the stack would be UPDATE_COMPLETE with a server waiting for someone."""
from services.orchestration.app import engine


class Api:
    def __init__(self, status):
        self.status, self.calls = status, []

    def call(self, svc, method, path, body=None, ok=(200, 201, 202, 204)):
        self.calls.append((method, path))
        return {"status": self.status} if method == "GET" else {}


def _update(status):
    api = Api(status)
    engine._server_u(api, "srv-1", {"flavor": "tiny"}, {"flavor": "medium"})
    return api.calls


def test_a_resized_server_in_verify_resize_is_confirmed():
    calls = _update("VERIFY_RESIZE")

    assert calls == [("POST", "/servers/srv-1/resize"), ("GET", "/servers/srv-1"), ("POST", "/servers/srv-1/confirmResize")]


def test_a_server_that_is_already_active_is_not_confirmed_again():
    assert ("POST", "/servers/srv-1/confirmResize") not in _update("ACTIVE")
