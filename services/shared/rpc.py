"""
cloudx shared RPC library — lightweight AMQP RPC over RabbitMQ
===============================================================

Provides two patterns inspired by oslo.messaging:

  RPCClient  — used by API services to publish messages
    .cast()  — fire-and-forget (async)
    .call()  — request-reply (sync, with timeout)

  RPCConsumer — used by workers/conductor to consume messages
    .run()   — blocking consume loop with auto-reconnect

Message format (JSON):
  {
    "id":             "msg-uuid",
    "method":         "build_instance",
    "args":           { ... },
    "reply_to":       "amq.gen-xxx" | null,
    "correlation_id": "corr-uuid"  | null,
    "timestamp":      1718398800.0
  }
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid

import pika
import pika.exceptions

log = logging.getLogger("cloudx.rpc")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@rabbitmq:5672/")
EXCHANGE     = "cloudx"                # default topic exchange
EVENT_EXCHANGE = "cloudx.events"       # fanout exchange for live events

RPC_CALL_TIMEOUT = int(os.getenv("RPC_CALL_TIMEOUT", "30"))


# ═══════════════════════════════════════════════════════════════════════════════
# RPCClient — publisher side
# ═══════════════════════════════════════════════════════════════════════════════

class RPCClient:
    """Publish RPC messages to RabbitMQ.

    Usage::

        rpc = RPCClient()
        rpc.cast("compute", "build_instance", {"server_id": "..."})
        result = rpc.call("compute", "get_logs", {"server_id": "..."}, timeout=30)
    """

    def __init__(self, rabbitmq_url: str | None = None, exchange: str = EXCHANGE):
        self._url = rabbitmq_url or RABBITMQ_URL
        self._exchange = exchange
        self._connection: pika.BlockingConnection | None = None
        self._channel: pika.channel.Channel | None = None
        self._lock = threading.Lock()

    # ── Connection management ─────────────────────────────────────────────────

    def _ensure_connection(self) -> None:
        if self._connection and self._connection.is_open:
            return
        params = pika.URLParameters(self._url)
        params.heartbeat = 600
        params.blocked_connection_timeout = 300
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        self._channel.exchange_declare(self._exchange, "topic", durable=True)
        self._channel.exchange_declare(EVENT_EXCHANGE, "fanout", durable=True)
        log.info("RPC client connected to %s", self._url)

    def _reconnect(self) -> None:
        try:
            if self._connection and not self._connection.is_closed:
                self._connection.close()
        except Exception:
            pass
        self._connection = None
        self._channel = None
        self._ensure_connection()

    # ── Cast (fire-and-forget) ────────────────────────────────────────────────

    def cast(self, routing_key: str, method: str, args: dict) -> None:
        """Asynchronous publish — returns immediately.

        The message is placed on the queue for the matching routing_key.
        No response is expected.
        """
        msg = {
            "id": str(uuid.uuid4()),
            "method": method,
            "args": args,
            "reply_to": None,
            "correlation_id": None,
            "timestamp": time.time(),
        }
        body = json.dumps(msg, default=str)
        with self._lock:
            for attempt in range(3):
                try:
                    self._ensure_connection()
                    self._channel.basic_publish(
                        exchange=self._exchange,
                        routing_key=routing_key,
                        body=body,
                        properties=pika.BasicProperties(
                            content_type="application/json",
                            delivery_mode=2,  # persistent
                        ),
                    )
                    log.debug("CAST %s.%s → %s", routing_key, method, msg["id"])
                    return
                except (pika.exceptions.AMQPConnectionError,
                        pika.exceptions.AMQPChannelError) as exc:
                    log.warning("Cast attempt %d failed: %s", attempt + 1, exc)
                    self._reconnect()
            raise RuntimeError(f"Failed to cast {method} after 3 attempts")

    # ── Call (request-reply) ──────────────────────────────────────────────────

    def call(self, routing_key: str, method: str, args: dict,
             timeout: int | None = None) -> dict:
        """Synchronous RPC — waits for worker reply up to ``timeout`` seconds.

        Creates an exclusive reply queue, publishes the message with
        ``reply_to`` set, and blocks until the correlated response arrives.
        """
        timeout = timeout or RPC_CALL_TIMEOUT
        correlation_id = str(uuid.uuid4())
        response: dict = {}

        with self._lock:
            self._ensure_connection()

            # Exclusive auto-delete reply queue
            result = self._channel.queue_declare("", exclusive=True, auto_delete=True)
            reply_queue = result.method.queue

            def _on_reply(ch, method_frame, props, body):
                if props.correlation_id == correlation_id:
                    response["data"] = json.loads(body)
                    ch.basic_cancel(method_frame.consumer_tag)

            self._channel.basic_consume(reply_queue, _on_reply, auto_ack=True)

            msg = {
                "id": str(uuid.uuid4()),
                "method": method,
                "args": args,
                "reply_to": reply_queue,
                "correlation_id": correlation_id,
                "timestamp": time.time(),
            }
            self._channel.basic_publish(
                exchange=self._exchange,
                routing_key=routing_key,
                body=json.dumps(msg, default=str),
                properties=pika.BasicProperties(
                    reply_to=reply_queue,
                    correlation_id=correlation_id,
                    content_type="application/json",
                    delivery_mode=1,  # non-persistent for calls
                ),
            )
            log.debug("CALL %s.%s → %s (timeout=%ds)", routing_key, method,
                       msg["id"], timeout)

            deadline = time.time() + timeout
            while "data" not in response:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"RPC call {routing_key}.{method} timed out after {timeout}s"
                    )
                self._connection.process_data_events(time_limit=min(remaining, 1.0))

        data = response["data"]
        if isinstance(data, dict) and "error" in data:
            raise RuntimeError(f"RPC {method} error: {data['error']}")
        return data

    # ── Event publishing (fanout) ─────────────────────────────────────────────

    def publish_event(self, event_type: str, payload: dict) -> None:
        """Publish a status-change event to the fanout exchange.

        Used by the conductor to broadcast events to the dashboard.
        """
        msg = {
            "type": event_type,
            "payload": payload,
            "timestamp": time.time(),
        }
        body = json.dumps(msg, default=str)
        with self._lock:
            for attempt in range(3):
                try:
                    self._ensure_connection()
                    self._channel.basic_publish(
                        exchange=EVENT_EXCHANGE,
                        routing_key="",  # fanout ignores routing key
                        body=body,
                        properties=pika.BasicProperties(
                            content_type="application/json",
                            delivery_mode=1,  # non-persistent events
                        ),
                    )
                    log.debug("EVENT %s published", event_type)
                    return
                except (pika.exceptions.AMQPConnectionError,
                        pika.exceptions.AMQPChannelError):
                    self._reconnect()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            if self._connection and not self._connection.is_closed:
                try:
                    self._connection.close()
                except Exception:
                    pass
            self._connection = None
            self._channel = None


# ═══════════════════════════════════════════════════════════════════════════════
# RPCConsumer — worker / conductor side
# ═══════════════════════════════════════════════════════════════════════════════

class RPCConsumer:
    """Long-running AMQP consumer with auto-reconnect.

    Usage::

        HANDLERS = {
            "build_instance": handle_build,
            "delete_instance": handle_delete,
        }
        consumer = RPCConsumer("compute", HANDLERS)
        consumer.run()   # blocks forever
    """

    def __init__(
        self,
        queue_name: str,
        handlers: dict[str, callable],
        rabbitmq_url: str | None = None,
        exchange: str = EXCHANGE,
        prefetch: int = 1,
    ):
        self.queue_name = queue_name
        self.handlers = handlers
        self._url = rabbitmq_url or RABBITMQ_URL
        self._exchange = exchange
        self._prefetch = prefetch
        self.logger = logging.getLogger(f"cloudx.rpc.{queue_name}")

    def run(self) -> None:
        """Blocking consume loop. Reconnects automatically on failure."""
        self.logger.info("Starting consumer for queue '%s'", self.queue_name)
        while True:
            try:
                self._consume_loop()
            except KeyboardInterrupt:
                self.logger.info("Consumer stopped (keyboard interrupt)")
                break
            except Exception as exc:
                self.logger.error("Consumer error: %s — reconnecting in 5s", exc)
                time.sleep(5)

    def _consume_loop(self) -> None:
        params = pika.URLParameters(self._url)
        params.heartbeat = 600
        params.blocked_connection_timeout = 300
        connection = pika.BlockingConnection(params)
        channel = connection.channel()

        # Declare exchange + queue + binding
        channel.exchange_declare(self._exchange, "topic", durable=True)
        channel.exchange_declare(EVENT_EXCHANGE, "fanout", durable=True)
        channel.queue_declare(self.queue_name, durable=True)
        channel.queue_bind(self.queue_name, self._exchange, self.queue_name)
        channel.basic_qos(prefetch_count=self._prefetch)

        self.logger.info("Consuming from queue '%s' (exchange=%s, prefetch=%d)",
                         self.queue_name, self._exchange, self._prefetch)

        def _on_message(ch, method_frame, props, body):
            msg = json.loads(body)
            method = msg.get("method", "?")
            args = msg.get("args", {})
            msg_id = msg.get("id", "?")

            handler = self.handlers.get(method)
            if handler is None:
                self.logger.warning("Unknown method '%s' (msg %s) — skipping",
                                     method, msg_id)
                ch.basic_ack(method_frame.delivery_tag)
                return

            self.logger.info("Handling %s (msg %s)", method, msg_id)
            try:
                result = handler(args)

                # If this was an RPC Call, send reply
                if props.reply_to:
                    ch.basic_publish(
                        exchange="",
                        routing_key=props.reply_to,
                        body=json.dumps(result or {}, default=str),
                        properties=pika.BasicProperties(
                            correlation_id=props.correlation_id,
                            content_type="application/json",
                        ),
                    )
                    self.logger.debug("Replied to %s for %s", props.reply_to, method)

            except Exception as exc:
                self.logger.error("Handler error for %s: %s", method, exc,
                                  exc_info=True)
                # Reply with error if this was an RPC Call
                if props.reply_to:
                    ch.basic_publish(
                        exchange="",
                        routing_key=props.reply_to,
                        body=json.dumps({"error": str(exc)}),
                        properties=pika.BasicProperties(
                            correlation_id=props.correlation_id,
                            content_type="application/json",
                        ),
                    )
            finally:
                ch.basic_ack(method_frame.delivery_tag)

        channel.basic_consume(self.queue_name, _on_message)
        channel.start_consuming()


# ═══════════════════════════════════════════════════════════════════════════════
# EventConsumer — dashboard side (fanout listener)
# ═══════════════════════════════════════════════════════════════════════════════

class EventConsumer:
    """Subscribes to the cloudx.events fanout exchange.

    Used by the dashboard to receive live status-change events.
    Runs in a background thread and calls ``on_event(msg_dict)``
    for each received event.
    """

    def __init__(
        self,
        on_event: callable,
        rabbitmq_url: str | None = None,
    ):
        self._on_event = on_event
        self._url = rabbitmq_url or RABBITMQ_URL
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.logger = logging.getLogger("cloudx.rpc.events")

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.logger.info("Event consumer started (background thread)")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.logger.info("Event consumer stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._consume()
            except Exception as exc:
                self.logger.error("Event consumer error: %s — reconnecting in 5s", exc)
                self._stop.wait(5)

    def _consume(self) -> None:
        params = pika.URLParameters(self._url)
        params.heartbeat = 600
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.exchange_declare(EVENT_EXCHANGE, "fanout", durable=True)

        # Exclusive auto-delete queue — each dashboard instance gets its own
        result = channel.queue_declare("", exclusive=True, auto_delete=True)
        queue_name = result.method.queue
        channel.queue_bind(queue_name, EVENT_EXCHANGE, "")

        self.logger.info("Listening for events on exclusive queue %s", queue_name)

        def _on_message(ch, method_frame, _props, body):
            if self._stop.is_set():
                ch.stop_consuming()
                return
            try:
                msg = json.loads(body)
                self._on_event(msg)
            except Exception as exc:
                self.logger.error("Event handler error: %s", exc)
            finally:
                ch.basic_ack(method_frame.delivery_tag)

        channel.basic_consume(queue_name, _on_message)

        while not self._stop.is_set():
            connection.process_data_events(time_limit=1)
        channel.stop_consuming()
        connection.close()
