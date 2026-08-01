---
name: RPC Messaging Standard
description: Guidelines for async communication between CloudX APIs and Workers using RabbitMQ.
---

# RPC Messaging Standard

CloudX uses AMQP over RabbitMQ for asynchronous operations (resembling `oslo.messaging`). When implementing features like Task-based Image Import or async Volume actions, follow these standards:

## 1. RPC Library Usage
- All inter-service communication should use the shared library `services/shared/rpc.py`.
- **`rpc.cast(topic, method, args)`**: Use for fire-and-forget background operations. This is the standard for long-running tasks like VM provisioning or volume creation. The API returns a transitional state immediately.
- **`rpc.call(topic, method, args, timeout)`**: Use only when a synchronous response is strictly required (e.g., fetching a VNC port or console logs). Be mindful of the timeout to prevent API blockages.

## 2. Payload Structure
- Ensure all arguments passed in the `args` dictionary are JSON-serializable. Avoid passing complex objects or database models. Pass UUIDs and let the worker fetch the necessary data.

## 3. Worker Implementation
- The consuming worker must define a method matching the exact name provided in the `method` argument.
- Workers must handle internal exceptions securely. If an async task fails, the worker must catch the error and update the resource's state in the database (e.g., from `BUILDING` to `ERROR` or `FAILED`) so the user is informed. Never let a task fail silently.

## 4. Event Notifications
- Follow the fanout event exchange pattern when resources change state so the dashboard can receive real-time updates via WebSocket.
- Use standard naming conventions for events (e.g., `compute.instance.create.end`).
