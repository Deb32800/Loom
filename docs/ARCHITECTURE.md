# Loom Architecture

Loom is a real-time collaborative text editor built on Operational
Transformation (OT). The README covers what it does and the headline
numbers; this document is about the part that's actually interesting —
how the pieces underneath fit together, and why they're shaped the way
they are. For the OT algorithm itself, read the docstrings in
`server/ot_engine.py` and `docs/loom_theory_guide.pdf`; this doc is about
everything built *around* that algorithm.

Live: [Azure (Australia East)](https://loom-backend-az.azurewebsites.net) ·
[Render (Singapore)](https://loom-fx82.onrender.com) — both run this exact
architecture, same Docker image. The README explains why there are two.

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

Every server instance runs identical code and can sit behind a load
balancer with no stickiness required — a client can hit any instance for
any request. That last part isn't free; it's the entire reason the Redis
layer exists. Coordination between instances happens *only* through
Redis — no instance ever calls another instance directly, because nothing
would tell you which instance to call.

## Request flow: connecting to a document

1. Client logs in over REST (`POST /api/auth/login`) and gets a
   short-lived JWT access token plus an httpOnly refresh-token cookie.
2. Client fetches a single-use WebSocket ticket for one specific document
   (`POST /api/documents/{id}/ws-ticket`, authenticated by the JWT).
   Browsers can't set custom headers on a WebSocket handshake, and putting
   a long-lived JWT in the query string would leak it into every access
   log the request touches — so a short-lived, one-time ticket stands in
   for it instead. Tickets live in Redis (`SETEX`, consumed atomically via
   `GETDEL`), which is *why* a client can fetch a ticket from one instance
   and connect to a completely different one — the ticket isn't tied to
   whichever process issued it.
3. Client opens `GET /ws?ticket=...`. The server validates and consumes
   the ticket, and pulls `document_id`, the caller's identity, and their
   role (owner/editor/viewer) out of it — never out of anything the client
   claims directly. A client cannot say "I'm the owner of document X";
   only a ticket the server itself issued can say that.
4. This instance tries to become — or confirms it already is — the
   document's **owner**, via `DocumentOwnership`, a Redis lock. If it's
   the owner, it loads the document into memory (`DocumentRegistry`) and
   that becomes the sync baseline. If some other instance already owns it,
   this instance reads that owner's state from a Redis-cached snapshot
   instead — it never loads its own `Document` object for a document it
   doesn't own, because that object would just be redundant and could
   drift out of sync with the real one.
5. Server sends the client a `sync` message (content, revision, who else
   is connected) and tells everyone else on the document that someone new
   joined, over `doc-control` pub/sub.
6. As the client types, it sends `operation` messages. If this instance
   owns the document, it applies the operation directly. If not, it
   forwards the operation to the owner over `doc-ops`; the owner's result
   comes back through `doc-broadcast`, which this instance is already
   subscribed to, and gets relayed locally as an `ack` (to whoever sent
   it) or an `operation` (to everyone else on that document).

## Why single-writer ownership, and not distributed OT

Here's the constraint everything else in this document exists to satisfy:
`Document.receive_operation()` — the actual transform-against-history
step — has to run in exactly one place at a time for a given document. If
two instances both tried to apply concurrent edits to the same document,
each transforming against its own local view of history, the two
instances' copies of that document could diverge permanently — and worse,
diverge silently, with no error to notice.

Making the OT transform itself distributed (so any instance could safely
apply an operation to any document, with the transform logic somehow
staying consistent across instances) is possible in theory. It's also not
what any real production collaborative editor actually does, and building
it would be solving a much harder problem than this project needs solved.
The simpler answer — and the one every real system converges on — is:
exactly one instance owns each document at a time. Every other instance
just forwards operations to whoever owns it, and relays that owner's
results back to its own locally-connected users.

Ownership itself is a Redis lease (`SET NX EX`, in
`server/document_ownership.py`), renewed on a heartbeat while held. If an
owning instance dies — or just misses a renewal because of a GC pause or
similar — it simply stops renewing, the lease expires on its own, and the
next instance to try for that document becomes the new owner. No manual
failover, no split-brain window longer than the lease TTL. Renew and
release both run as Lua scripts so an instance can only ever touch a lock
it actually still holds (a compare-and-set against the stored instance
id) — it's not possible for an instance to accidentally release or renew
a lock that belongs to someone else. And losing a lease involuntarily
(a missed renewal, as opposed to a clean `release()`) fires an `on_lost`
callback, so the now-stale `doc-ops` subscription for that document gets
torn down in the same place — otherwise you'd end up with an instance
subscribed to messages it has no right to act on anymore.

## Redis fanout (`server/fanout.py`)

Three pub/sub channels exist per document, and each one earns its place:

| Channel | Publisher | Subscriber | Purpose |
|---|---|---|---|
| `doc-ops:{id}` | any non-owner instance | the owner | forward a client operation to whoever can actually process it |
| `doc-broadcast:{id}` | the owner | every instance with local clients on that doc | the processed result — relayed as `ack` to the sender or `operation` to everyone else |
| `doc-control:{id}` | any instance | every instance with local clients on that doc | presence join/leave, `document_deleted` |

Plus one plain key (not pub/sub) per document, `doc-state:{id}` —
`{"content": ..., "revision": ...}` — kept fresh by the owner on every
applied operation. Non-owner instances read this as the sync baseline for
a newly connecting client, since they don't have an in-memory `Document`
of their own to read from.

**Two things this deliberately does not do**, and why that's a choice
rather than an oversight:
- **Cursor position and the full cross-instance client list aren't fanned
  out** — they're local-only, per instance. They're frequent (every mouse
  click, practically), cosmetic, and don't affect document correctness at
  all. Fanning them out over Redis would mean steady traffic for
  something nobody actually needs to be perfectly consistent, unlike
  operations, ownership, and presence, all of which are comparatively
  rare and actually matter.
- **If an owning instance's local clients all disconnect while a
  *different* instance still has clients on that same document,**
  ownership releases (tied to `DocumentRegistry`'s own eviction grace
  period), and the document sits briefly unowned until a new connection
  re-acquires it. Anything forwarded in that gap gets dropped rather than
  queued. Real clients reconnect on their own, and making this gap
  perfectly lossless would need either document-aware load-balancer
  routing or a request/response protocol layered on top of pub/sub —
  both meaningfully bigger than what this project's scope calls for.

There's a real story attached to this file: an earlier version started
the fanout's listen loop *before any channel had ever been subscribed to*.
redis-py's async `PubSub.listen()` is implemented as `while self.subscribed:
...` — so starting it with zero subscriptions meant the loop exited
immediately, and the task quietly finished within milliseconds of server
boot, with no error anywhere. Every subscription made after that point
succeeded fine on Redis's side; there just wasn't anything left running
`.listen()` to ever read them back. The fix — subscribe to one permanent,
never-unsubscribed keepalive channel before starting the listen task — is
a two-line change, but finding it took reproducing the bug in isolation
against a live Redis instance and reading redis-py's own source. The
README's performance section has the numbers from finding it; this is
the actual mechanism.

## Document lifecycle (`server/document_registry.py`)

Documents load from Postgres lazily, on first connection — nothing is
loaded eagerly at startup — and get evicted from memory (after a final
flush) once the last connected client disconnects **and** a grace period
elapses (`EVICTION_GRACE_SECONDS = 60`). That grace period exists so a
quick reconnect doesn't pay the reload cost, while an abandoned document
still doesn't sit in memory forever. Eviction fires an `on_evict`
callback so `main.py` can release the Redis ownership lock and the
`doc-ops` subscription in the same place — `DocumentRegistry` itself
doesn't need to know Redis exists at all.

Operations are batched to the database rather than written synchronously
on every keystroke — `main.py`'s `db_flush_loop` flushes pending
operations and touched documents' state every 2 seconds. This is the
difference between one database write per character typed and one write
per (up to) 2 seconds of activity, and it's most of why Postgres write
load isn't a bottleneck here. On shutdown, every active document gets
flushed, every ownership lock this instance held gets released (so
another instance can take over immediately instead of waiting out the
full lease TTL), and Redis/DB connections close cleanly.

## Access control (`server/documents.py`)

Every document has exactly one owner (set at creation) and zero or more
additional members, added via `POST /{id}/share`, with role `editor` or
`viewer`. Access — including opening the WebSocket — always goes through
a `document_members` row. There's no implicit access anywhere in this
system; if you're not a member, the document doesn't exist as far as the
API is concerned. Viewers can connect and watch updates in real time, but
get rejected server-side the moment they try to send an actual edit.

## Persistence (`server/database.py`, `server/models.py`)

PostgreSQL via SQLAlchemy 2.0's async ORM. Schema is owned by Alembic
(`alembic/versions/`), but `Database.initialize()` also runs `create_all`
so a bare clone works without running migrations first — a dev/test
convenience. Alembic and `create_all` both describe the same schema in
`server/models.py`, so the two can't quietly drift apart from each other.

Tables: `users`, `refresh_tokens`, `documents`, `document_members`,
`operations` — that last one holds the full operation history per
document, which is what makes it possible to correctly transform a
late-arriving client operation against everything that happened while it
was in flight.

## What stays local-only per instance, on purpose

- `SessionManager` (`server/session_manager.py`) — which clients are
  connected *to this instance specifically*: their WebSocket objects,
  cursor positions, colors, roles. Never shared across instances, and
  never needs to be — each instance only has to know about its own
  locally-connected clients.
- The in-memory `Document` object itself. Only the current owner has one;
  everyone else works from the Redis-cached snapshot instead.

## Not covered here

The client-side OT state machine (`client/js/ot_client.js`,
`ot_transform.js`) and the token-refresh flow (`server/auth.py`) are
documented in their own module docstrings — this file is specifically
about the server-side, multi-instance design. The README's
[Performance](../README.md#performance-what-actually-happened-when-we-measured-it)
section has the actual measured numbers (latency comparison across
regions, concurrency test results) that this architecture makes possible.
