import pytest
from shared import rpc


def test_host_queue_addresses_one_node_and_falls_back_to_the_shared_queue():
    assert rpc.host_queue("compute", "cloudx-storage-01") == "compute.cloudx-storage-01"
    assert rpc.host_queue("compute", None) == "compute"
    assert rpc.host_queue("block-store", "") == "block-store"


class _Stop(Exception):
    pass


class FakeChannel:
    def __init__(self):
        self.declared, self.bound, self.consumed = [], [], []

    def exchange_declare(self, *a, **k):
        pass

    def queue_declare(self, queue, **k):
        self.declared.append(queue)

    def queue_bind(self, queue, exchange, routing_key):
        self.bound.append((queue, routing_key))

    def basic_qos(self, **k):
        pass

    def basic_consume(self, queue, callback):
        self.consumed.append(queue)

    def start_consuming(self):
        raise _Stop


def test_consumer_listens_on_its_own_queue_and_the_shared_one(monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr(rpc.pika, "URLParameters", lambda url: type("P", (), {})())
    monkeypatch.setattr(rpc.pika, "BlockingConnection", lambda params: type("C", (), {"channel": lambda self: channel})())

    consumer = rpc.RPCConsumer("compute.node-a", {}, rabbitmq_url="amqp://x", extra_queues=("compute",))
    with pytest.raises(_Stop):
        consumer._consume_loop()

    assert channel.declared == ["compute.node-a", "compute"]
    assert channel.bound == [("compute.node-a", "compute.node-a"), ("compute", "compute")]  # a queue's routing key is its own name
    assert channel.consumed == ["compute.node-a", "compute"]
