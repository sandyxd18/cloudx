"""A synchronous RPC on a connection the broker side already closed (HAProxy drops an idle AMQP connection after its
`timeout client`) must reconnect and go through, as `cast` already does; a call that was published must never be sent twice."""
import json
from types import SimpleNamespace

import pika
import pytest
from shared import rpc

_state = {"created": [], "next_fail": None}


class _Conn:
    """A fake pika connection. `fail_at` is where a stale connection breaks: 'declare', 'publish' or 'wait'."""

    def __init__(self, fail_at=None):
        self.fail_at, self.is_open, self.is_closed = fail_at, True, False
        self.published, self._cb, self._corr = 0, None, None
        _state["created"].append(self)

    def channel(self):
        return self

    def exchange_declare(self, *a, **k):
        pass

    def queue_declare(self, queue, **k):
        if self.fail_at == "declare":
            raise pika.exceptions.StreamLostError("Transport indicated EOF")
        return SimpleNamespace(method=SimpleNamespace(queue="amq.gen-reply"))

    def basic_consume(self, queue, cb, **k):
        self._cb = cb

    def basic_cancel(self, tag):
        pass

    def basic_publish(self, exchange, routing_key, body, properties):
        if self.fail_at == "publish":
            raise pika.exceptions.StreamLostError("Broken pipe")
        self.published += 1
        self._corr = properties.correlation_id

    def process_data_events(self, time_limit=0):
        if self.fail_at == "wait":
            raise pika.exceptions.StreamLostError("Transport indicated EOF")
        props = SimpleNamespace(correlation_id=self._corr)
        self._cb(self, SimpleNamespace(consumer_tag="t"), props, json.dumps({"ok": True}).encode())

    def close(self):
        self.is_open, self.is_closed = False, True


@pytest.fixture
def client(monkeypatch):
    _state["created"] = []

    def make(params):
        return _Conn(fail_at=_state["next_fail"] if _state["created"] == [] else None)

    monkeypatch.setattr(rpc.pika, "BlockingConnection", make)
    monkeypatch.setattr(rpc, "_connection_params", lambda *a, **k: None)
    return rpc.RPCClient("amqp://x")


@pytest.mark.parametrize("where", ["declare", "publish"])
def test_a_call_on_a_stale_connection_reconnects_and_goes_through(client, where):
    _state["next_fail"] = where
    assert client.call("compute.host", "get_console_info", {}) == {"ok": True}
    assert len(_state["created"]) == 2
    assert _state["created"][0].published == 0 and _state["created"][1].published == 1


def test_a_call_that_was_published_is_not_sent_again(client):
    _state["next_fail"] = "wait"
    with pytest.raises(pika.exceptions.AMQPError):
        client.call("compute.host", "stop_instance", {})
    assert sum(c.published for c in _state["created"]) == 1
