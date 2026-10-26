"""A broker that dies or reboots without closing its connections must be noticed in about a minute.

AMQP heartbeats are 600 s because a handler (a build waits up to 300 s for a volume) sends none, so a consumer only
found out a controller had crashed when its next heartbeat hit the rebooted host, up to heartbeat/2 = 5 minutes later,
and builds sat in the queue until then. TCP keepalive is done by the kernel, whatever the handler is doing."""
import logging
import socket
from types import SimpleNamespace

import pika.tcp_socket_opts
import pytest
from shared import rpc


def test_the_tcp_options_are_ones_the_kernel_and_pika_accept():
    sock = socket.socket()
    try:
        pika.tcp_socket_opts.set_sock_opts(rpc.TCP_OPTIONS, sock)

        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) == 30
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL) == 10
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT) == 3
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT) == 60000
    finally:
        sock.close()


def test_pika_does_not_reject_any_of_the_tcp_options(caplog):
    with caplog.at_level(logging.WARNING, logger="pika.tcp_socket_opts"):
        pika.tcp_socket_opts.set_sock_opts(rpc.TCP_OPTIONS, socket.socket())
    assert "Unsupported TCP option" not in caplog.text


class _Channel:
    def __init__(self):
        self.consumed = []

    def exchange_declare(self, *a, **k):
        pass

    def queue_declare(self, queue, **k):
        return SimpleNamespace(method=SimpleNamespace(queue=queue or "amq.gen-test"))

    def queue_bind(self, *a, **k):
        pass

    def basic_qos(self, **k):
        pass

    def basic_consume(self, queue, callback):
        self.consumed.append(queue)

    def start_consuming(self):
        raise _Stop

    def stop_consuming(self):
        pass


class _Stop(Exception):
    pass


class _Connection:
    is_open = True

    def __init__(self, params, seen):
        seen.append(params)
        self._channel = _Channel()

    def channel(self):
        return self._channel

    def process_data_events(self, time_limit=0):
        pass

    def close(self):
        pass


@pytest.fixture
def connections(monkeypatch):
    seen = []
    monkeypatch.setattr(rpc.pika, "URLParameters", lambda url: SimpleNamespace(url=url))
    monkeypatch.setattr(rpc.pika, "BlockingConnection", lambda params: _Connection(params, seen))
    return seen


def test_the_rpc_client_connects_with_keepalive_and_the_long_heartbeat(connections):
    rpc.RPCClient("amqp://cloudx@10.0.0.1/")._ensure_connection()

    assert connections[0].tcp_options == rpc.TCP_OPTIONS
    assert connections[0].heartbeat == 600


def test_the_rpc_consumer_connects_with_keepalive_and_the_long_heartbeat(connections):
    consumer = rpc.RPCConsumer("compute.node-a", {}, rabbitmq_url="amqp://cloudx@10.0.0.1/")
    with pytest.raises(_Stop):
        consumer._consume_loop()

    assert connections[0].tcp_options == rpc.TCP_OPTIONS
    assert connections[0].heartbeat == 600


def test_the_event_consumer_connects_with_keepalive_and_the_long_heartbeat(connections):
    consumer = rpc.EventConsumer(lambda event: None, rabbitmq_url="amqp://cloudx@10.0.0.1/")
    consumer._stop.set()  # connect and declare, then leave the consume loop at once
    consumer._consume()

    assert connections[0].tcp_options == rpc.TCP_OPTIONS
    assert connections[0].heartbeat == 600
