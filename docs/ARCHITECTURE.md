# Loom Architecture

Loom is a real-time collaborative text editor built on Operational
Transformation (OT). This document describes how the pieces fit together —
for the OT algorithm itself, see the docstrings in `server/ot_engine.py` and
`docs/loom_theory_guide.pdf`; this doc is about everything built around it.

## System overview

```
Browser ──REST (JWT)──► FastAPI (server/auth.py, server/documents.py)
   │                          │
   │                          ▼
   │                     PostgreSQL (users, documents, members, operations,
   │                                 refresh tokens)
   │
   └──WebSocket (ticket)──► FastAPI /ws (server/main.py)
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼              ▼
              DocumentOwnership  DocumentRegistry  RedisFanout
              (who owns what)    (loaded Documents) (cross-instance pub/sub)
                    │             │              │
                    └─────────────┴──────────────┘
                                  ▼
                              Redis (locks, ws-tickets, pub/sub, cached state)
```

Every server instance runs the same code and can be reached behind a load
balancer. Coordination between instances — who processes which document's
edits, how forwarded operations and their results move between instances —
happens entirely through Redis. No instance-to-instance RPC.

## Request flow: connecting to a document

1. Client logs in over REST (`POST /api/auth/login`), getting a short-lived
   JWT access token plus an httpOnly refresh-token cookie.
2. Client fetches a single-use WebSocket ticket for one specific document
   (`POST /api/documents/{id}/ws-ticket`, authenticated by the JWT). Browsers
   can't set custom headers on a WS handshake, and a long-lived JWT in the
   query string would leak into access logs, so a short-lived one-time
   ticket stands in for it instead. Tickets live in Redis (`SETEX`, consumed
   atomically via `GETDEL`) so a client can fetch a ticket from one instance
   and connect to a different one.
3. Client opens `GET /ws?ticket=...`. The server validates and consumes the
   ticket, deriving `document_id`, the caller's identity, and their role
   (owner/editor/viewer) from it — never from anything the client claims
   directly.
4. This instance tries to become (or confirms it already is) the document's
   **owner** via `DocumentOwnership`, a Redis lock. Owner: loads the document
   into memory via `DocumentRegistry` and uses that as the sync baseline.
   Non-owner: reads the current owner's state from a Redis-cached snapshot
   instead — it never loads its own `Document` object for a document it
   doesn't own.
5. Server sends the client a `sync` message (content, revision, who else is
   connected) and tells everyone else on the document that a new user joined
   (`doc-control` pub/sub).
6. As the client types, it sends `operation` messages. If this instance owns
   the document, it applies the operation directly. If not, it forwards the
   operation to the owner over Redis (`doc-ops`); the owner's result comes
   back through `doc-broadcast`, which this instance is already subscribed
   to, and gets relayed locally as an `ack` (to the sender) or `operation`
   (to everyone else).

## Why single-writer ownership, not distributed OT

`Document.receive_operation()` — the transform-against-history step — must
run in exactly one place at a time for a given document, or two instances
could transform two concurrent edits against different histories and
diverge. Making the OT transform itself distributed is possible in theory
but is not what production editors actually do in practice, and is well
beyond what this project's scope calls for. Instead, exactly one instance
becomes the "owner" of each document at a time; every other instance
forwards operations to it and relays its broadcasts locally.

Ownership is a Redis lease (`SET NX EX`, `server/document_ownership.py`),
renewed on a heartbeat while held. If an owning instance dies (or a GC pause
makes it miss a renewal), it simply stops renewing and the lease expires —
the next instance to try for that document becomes the new owner. Renew and
release both use Lua scripts so an instance can only ever touch a lock it
still actually holds (compare-and-set on the stored instance id), never
someone else's. Losing a lease involuntarily (missed renewal, as opposed to
an explicit `release()`) fires an `on_lost` callback so the owning
instance's now-stale `doc-ops` subscription gets torn down too, instead of
leaking a subscription that only ever sees messages it has to ignore.

## Redis fanout (`server/fanout.py`)

Three pub/sub channels per document:

| Channel | Publisher | Subscriber | Purpose |
|---|---|---|---|
| `doc-ops:{id}` | any non-owner instance | the owner | forward a client operation to whoever can process it |
| `doc-broadcast:{id}` | the owner | every instance with local clients on that doc | the processed result — relayed as `ack` to the sender or `operation` to everyone else |
| `doc-control:{id}` | any instance | every instance with local clients on that doc | presence join/leave, `document_deleted` |

Plus one plain key (not pub/sub) per document, `doc-state:{id}` —
`{"content": ..., "revision": ...}` — kept fresh by the owner on every
applied operation, used by non-owner instances as the sync baseline for a
newly connecting client (since they have no in-memory `Document` of their
own to read from).

**Deliberately out of scope:**
- **Cursor position** and the **full cross-instance client list** are
  local-only per instance, not fanned out. They're frequent, cosmetic, and
  non-authoritative; fanning them out would add steady Redis traffic for
  something that doesn't affect document correctness, unlike ops, ownership,
  and presence (all comparatively rare).
- If the owning instance's local clients all disconnect while a *different*
  instance still has clients on the same document, ownership releases (tied
  to `DocumentRegistry`'s own eviction grace period) and the document is
  briefly unowned until a new connection re-acquires it. A forward that
  lands in that gap is dropped rather than queued — real clients reconnect,
  and handling it losslessly would need either document-aware load-balancer
  routing or a request/response protocol on top of pub/sub, both bigger than
  this project's scope calls for.

## Document lifecycle (`server/document_registry.py`)

Documents are loaded from Postgres lazily, on first connection, and evicted
from memory (after a final flush) once the last connected client
disconnects **and** a grace period elapses (`EVICTION_GRACE_SECONDS = 60`) —
so a quick reconnect doesn't pay the reload cost, but an abandoned document
doesn't sit in memory forever. Eviction fires an `on_evict` callback so
`main.py` can release the Redis ownership lock and `doc-ops` subscription in
the same place, without `DocumentRegistry` needing to know anything about
Redis itself.

Operations are batched to the database rather than written synchronously:
`main.py`'s `db_flush_loop` flushes pending operations and touched
documents' state every 2 seconds. On shutdown, every active document is
flushed, every ownership lock this instance held is released (so another
instance can take over immediately instead of waiting out the lease TTL),
and Redis/DB connections are closed.

## Access control (`server/documents.py`)

Every document has exactly one owner (set at creation) and zero or more
additional members (added via `POST /{id}/share`) with role `editor` or
`viewer`. Access — including opening the WebSocket — always goes through a
`document_members` row; there is no implicit access. Viewers can connect and
receive updates but are rejected server-side if they send an `operation`
message.

## Persistence (`server/database.py`, `server/models.py`)

PostgreSQL via SQLAlchemy 2.0's async ORM. Schema is owned by Alembic
(`alembic/versions/`); `Database.initialize()` also runs `create_all` so a
bare clone works without running migrations first (dev/test convenience) —
Alembic and `create_all` describe the same schema in `server/models.py`, so
they can't drift apart.

Tables: `users`, `refresh_tokens`, `documents`, `document_members`,
`operations` (the full op history per document, used to transform
late-arriving client operations against what happened while they were
offline).

## What's local-only per instance

- `SessionManager` (`server/session_manager.py`) — which clients are
  connected *to this instance*, their WebSocket objects, cursor positions,
  colors. Never shared across instances; each instance only knows about its
  own locally-connected clients.
- The in-memory `Document` object itself — only the current owner has one at
  all.

## Not yet covered here

Client-side OT state machine (`client/js/ot_client.js`,
`ot_transform.js`) and the auth token refresh flow
(`server/auth.py`) are documented in their own module docstrings; this file
focuses on the server-side, multi-instance architecture.
