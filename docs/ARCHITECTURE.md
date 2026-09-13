# Loom Architecture

How Loom's server side actually fits together. For the OT algorithm
itself, read `server/ot_engine.py`'s docstrings and `docs/loom_theory_guide.pdf` —
this doc is about everything built around it.

Live: [Azure (Australia East)](https://loom-backend-az.azurewebsites.net) ·
[Render (Singapore)](https://loom-fx82.onrender.com) — same code, same
Docker image, different regions. The README's Performance section explains
why both stay up.

## System overview

```
                             ┌─────────┐
                             │ Browser │
                             └─────────┘
                                  │
                  REST (JWT)  +  WebSocket (ticket)
                                  ▼
                      ┌────────────────────────┐
                      │    FastAPI instance    │
                      │ auth.py + documents.py │
                      │  main.py  (/ws route)  │
                      └────────────────────────┘
                                  │
                      ┌────────────┴────────────┐
                      ▼                         ▼
            ┌──────────────────┐      ┌──────────────────┐
            │      Redis       │      │    PostgreSQL    │
            │ ownership locks  │      │ users, documents │
            │  pub/sub fanout  │      │ document_members │
            │ ws-ticket store  │      │    operations    │
            │ cached doc state │      └──────────────────┘
            └──────────────────┘
```

Every instance runs identical code and sits behind a load balancer with no
stickiness — any instance can take any request. That only works because
Redis carries all the coordination; instances never call each other
directly.

## Request flow: connecting to a document

1. Client logs in (`POST /api/auth/login`) → short-lived JWT + httpOnly
   refresh cookie.
2. Client requests a single-use WebSocket ticket
   (`POST /api/documents/{id}/ws-ticket`). Browsers can't set custom
   headers on a WS handshake, and a JWT in the URL would leak into access
   logs — so a one-time ticket stands in instead. Tickets live in Redis
   (`SETEX`, consumed via `GETDEL`), which is why a client can fetch a
   ticket from one instance and connect through a different one.
3. Client opens `GET /ws?ticket=...`. The server derives `document_id`,
   identity, and role from the ticket itself — never from anything the
   client claims directly.
4. This instance tries to become (or confirms it already is) the
   document's **owner** via a Redis lock. Owner → loads the document into
   memory, that's the sync baseline. Non-owner → reads the owner's cached
   state from Redis instead, rather than loading a redundant copy.
5. Server sends `sync` (content, revision, who's connected) and tells
   everyone else a new client joined.
6. Client edits send `operation` messages. Owner → applies directly.
   Non-owner → forwards to the owner over Redis; the result comes back
   over the broadcast channel this instance already subscribes to.

## Why single-writer, not distributed OT

`Document.receive_operation()` — the transform-against-history step —
has to run in exactly one place per document, or two instances could
transform concurrent edits against different histories and the document
would silently diverge. Making the transform itself distributed is
possible in theory; no real collaborative editor actually does it, because
it's solving a much harder problem for no real benefit. So: exactly one
instance owns each document at a time, everyone else just forwards to it.

Ownership is a Redis lease (`SET NX EX`, `document_ownership.py`), renewed
on a heartbeat. If an instance dies or misses a renewal, the lease expires
on its own and the next instance to try takes over — no manual failover.
Renew/release run as Lua scripts so an instance can only ever touch a lock
it still actually holds. Losing a lease involuntarily also tears down that
document's now-stale `doc-ops` subscription, so nothing's left listening
for messages it has no right to act on.

## Redis fanout (`server/fanout.py`)

| Channel | Publisher | Subscriber | Purpose |
|---|---|---|---|
| `doc-ops:{id}` | non-owner instance | the owner | forward an edit to whoever can process it |
| `doc-broadcast:{id}` | the owner | instances with local clients on that doc | the result — `ack` to the sender, `operation` to everyone else |
| `doc-control:{id}` | any instance | instances with local clients on that doc | presence join/leave, `document_deleted` |

Plus `doc-state:{id}` — a plain key, not pub/sub — kept fresh by the owner
so non-owner instances have a sync baseline without needing their own
`Document` object.

**Deliberately not fanned out:** cursor position and the full client list.
Frequent, cosmetic, don't affect correctness — not worth the steady Redis
traffic. **Deliberately lossy in one narrow case:** if a document's owner
loses all local clients while another instance still has some, there's a
brief unowned gap before re-acquisition; a forward landing in that gap
gets dropped rather than queued. Real clients reconnect; making this
lossless needs machinery bigger than this project calls for.

**The bug this file actually had:** the fanout's listen loop used to start
before any channel was ever subscribed. redis-py's `PubSub.listen()` is
`while self.subscribed: ...` — zero subscriptions at start meant the loop
exited immediately and the task quietly died within milliseconds of boot,
no error anywhere. Every later subscription worked fine on Redis's side;
nothing was left running `.listen()` to read it back. Fix: subscribe to a
permanent keepalive channel before starting the loop. Two lines — finding
it took reproducing the failure against a live Redis instance and reading
redis-py's own source.

## Document lifecycle (`document_registry.py`)

Documents load from Postgres lazily, on first connection, and get evicted
from memory (after a final flush) once the last client disconnects **and**
a 60-second grace period passes — a quick reconnect skips the reload, an
abandoned document doesn't sit in memory forever. Eviction fires a
callback so `main.py` releases the ownership lock and `doc-ops`
subscription in the same place, without the registry needing to know
Redis exists.

Writes are batched, not synchronous: `db_flush_loop` flushes every 2
seconds — one write per document per interval instead of one per
keystroke. On shutdown, everything active gets flushed and every held
lock gets released immediately rather than waiting out its TTL.

## Access control (`documents.py`)

One owner per document, set at creation; `editor`/`viewer` members added
via `POST /{id}/share`. Access — including opening the WebSocket — always
goes through a `document_members` row; there's no implicit access.
Viewers can watch in real time but get rejected server-side the moment
they try to send an edit.

## Persistence

PostgreSQL via SQLAlchemy 2.0's async ORM, schema owned by Alembic. `Database.initialize()`
also runs `create_all` so a bare clone works without migrating first —
Alembic and `create_all` describe the same schema, so they can't drift.

Tables: `users`, `refresh_tokens`, `documents`, `document_members`,
`operations` — the last one is the full per-document edit history, used
to transform a late-arriving operation against everything that happened
while it was in flight.

## Local-only per instance

- `SessionManager` — which clients are connected *to this instance*:
  sockets, cursors, colors. Never shared; each instance only tracks its
  own.
- The in-memory `Document` object itself — only the owner has one.

## Not covered here

Client-side OT state machine (`client/js/ot_client.js`) and the token
refresh flow are documented in their own files. The README's
[Performance](../README.md#performance-what-actually-happened-when-we-measured-it)
section has the actual measured numbers this architecture produces.
