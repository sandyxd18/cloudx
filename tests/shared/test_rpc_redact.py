"""The broker URL carries the RabbitMQ password; it must never reach a log line."""
import logging

import pytest
from shared import rpc


@pytest.mark.parametrize("url, expected", [
    ("amqp://cloudx:S3cretPw@10.0.0.1:5672/", "amqp://cloudx:***@10.0.0.1:5672/"),
    ("amqps://cloudx:S3cretPw@mq.example.com:5671/%2F", "amqps://cloudx:***@mq.example.com:5671/%2F"),
    ("amqp://cloudx:p%40ss%3Aw%2Frd@10.0.0.1:5672/", "amqp://cloudx:***@10.0.0.1:5672/"),
    ("amqp://cloudx:a@b@10.0.0.1:5672/", "amqp://cloudx:***@10.0.0.1:5672/"),
    ("amqp://cloudx@10.0.0.1:5672/", "amqp://cloudx@10.0.0.1:5672/"),
    ("amqp://10.0.0.1:5672/", "amqp://10.0.0.1:5672/"),
    ("", ""),
])
def test_redact_url_hides_only_the_password(url, expected):
    assert rpc.redact_url(url) == expected


class _Channel:
    def exchange_declare(self, *a, **k):
        pass


class _Connection:
    is_open = True

    def channel(self):
        return _Channel()


def test_the_rpc_client_does_not_log_the_password_when_it_connects(monkeypatch, caplog):
    monkeypatch.setattr(rpc.pika, "BlockingConnection", lambda params: _Connection())
    client = rpc.RPCClient("amqp://cloudx:S3cretPw@10.0.0.1:5672/")

    with caplog.at_level(logging.DEBUG, logger="cloudx.rpc"):
        client._ensure_connection()

    assert "10.0.0.1" in caplog.text, "the log line should still say where it connected"
    assert "S3cretPw" not in caplog.text
