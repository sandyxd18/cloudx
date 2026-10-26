# Messaging (Zaqar-like)

Per-project message queues for tenant applications. Producers post, consumers claim and then delete the messages they have processed. A message whose claim expires before it is deleted shows up again (at-least-once).

## Architecture

- `messaging-api` (controller, port 8022, `User=cloudx`), tables `msg_queues` and `msg_messages` in PostgreSQL. No worker, background thread or new credentials.
- Claims use `SELECT ... FOR UPDATE SKIP LOCKED`, so several gunicorn workers never get the same message.
- FIFO order per queue (ascending id). No strict guarantee between parallel claims.
- Expired messages are never returned (filtered when read) and are deleted on the next `POST` to that queue.

## Size limits

JSON body at most 64 KiB; message `ttl` 60 seconds to 14 days (default 1 hour); claim `ttl` 30 seconds to 12 hours (default 5 minutes); at most 10 messages per `POST`, 20 per claim, 10,000 messages per queue (beyond that, 429).

## API

```
PUT    /queues/{name}                       GET /queues        GET /queues/{name}/stats
DELETE /queues/{name}
POST   /queues/{name}/messages              {"messages":[{"body":..., "ttl":3600}]}
GET    /queues/{name}/messages?limit=       (peek without claiming)
POST   /queues/{name}/claims?ttl=&limit=    -> {"claim_id", "items"}
DELETE /queues/{name}/messages/{id}?claim_id=
DELETE /queues/{name}/claims/{claim_id}     (release a claim)
```

Deleting a message that is currently claimed must include the matching `claim_id` (409 otherwise). A queue is referred to by name and is only visible in its owner project; the `reader` role can only read.

## CLI

```
cloudx queue create jobs
cloudx queue post jobs '{"task": "resize", "id": 7}' --ttl 600
cloudx queue claim jobs --ttl 60 --limit 5
cloudx queue ack jobs <message_id> --claim <claim_id>
cloudx queue list | stats | peek | delete
```

## Configuration

`messaging_api_url` in `cloudx.conf`. Dashboard: the Queues page (Compute).

## Limits

No topics, subscriptions, webhooks, push notifications, delayed messages, dead-lettering, message encryption, or quotas other than 10,000 per queue. Messages are stored in the controller's PostgreSQL, so this is not a high-throughput broker. The dashboard only creates queues, shows counts and deletes them; posting and claiming go through the API or CLI.
