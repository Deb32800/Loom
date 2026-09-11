# Loom

**Real-Time Collaborative Document Editor**

Loom is a multi-document collaborative text editor: sign up, create
documents, share them with other users as editors or viewers, and edit
together in real time. Concurrent edits are reconciled with Operational
Transformation (OT), and the server is designed to run as multiple
instances behind a load balancer, coordinated through Redis.

---

## Key Features

- **Operational Transformation**: accurate resolution of concurrent edits.
- **Multi-document workspace**: create, rename, delete, and share documents
  with owner/editor/viewer roles.
- **JWT authentication**: signup/login with argon2id password hashing,
  short-lived access tokens, rotated refresh tokens.
- **Real-time sync**: WebSockets, single-use tickets for the handshake.
- **Multi-instance ready**: document ownership and cross-instance
  fanout run through Redis, so the app isn't pinned to one process — see
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- **Persistence**: PostgreSQL via SQLAlchemy's async ORM, schema managed by
  Alembic.

## Technology Stack

- **Backend**: Python 3.9+, FastAPI, Uvicorn
- **Persistence**: PostgreSQL (SQLAlchemy async + asyncpg), Alembic migrations
- **Coordination**: Redis (WS-ticket store, document ownership locks,
  cross-instance pub/sub fanout)
- **Auth**: JWT access tokens, argon2id password hashing
- **Frontend**: HTML5, CSS3, vanilla JavaScript

## Project Structure

```
Loom/
├── server/
│   ├── ot_engine.py          # OT operations & transform functions
│   ├── document.py           # Document state management
│   ├── document_registry.py  # Loads/caches/evicts in-memory Document instances
│   ├── document_ownership.py # Single-writer-per-document Redis lock
│   ├── fanout.py             # Cross-instance pub/sub (ops, broadcast, presence)
│   ├── wire_format.py        # JSON <-> OT operation conversion
│   ├── session_manager.py    # Connected clients, scoped per document
│   ├── database.py           # PostgreSQL persistence layer
│   ├── models.py             # SQLAlchemy ORM models
│   ├── redis_client.py       # Shared Redis connection
│   ├── auth.py                # JWT auth, refresh tokens, WS tickets
│   ├── documents.py          # Multi-document REST API (CRUD, sharing)
│   └── main.py               # FastAPI application & WebSocket endpoint
├── client/
│   ├── index.html            # Login/dashboard/editor single-page app
│   ├── css/editor.css
│   └── js/
│       ├── app.js            # Application wiring
│       ├── auth.js           # Login/signup/token handling
│       ├── dashboard.js      # Document list, create/share/delete
│       ├── websocket.js      # WebSocket client connection manager
│       ├── ot_client.js      # OT client-side state machine
│       ├── ot_transform.js   # Client-side transform helpers
│       └── editor.js         # Textarea input event handler
├── alembic/                  # Database migrations
├── docs/
│   └── ARCHITECTURE.md       # Multi-instance server architecture
├── tests/
│   ├── test_ot_engine.py     # OT correctness tests
│   └── test_document.py      # Document state tests
├── docker-compose.yml        # Local Postgres + Redis
├── requirements.txt
└── README.md
```

## Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/Deb32800/Loom.git
cd Loom
```

### 2. Set Up the Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Start Postgres and Redis
```bash
docker compose up -d
```
This starts Postgres on `5432` and Redis on `6379` with the defaults
`server/database.py` and `server/redis_client.py` expect
(`DATABASE_URL`, `REDIS_URL` env vars override them).

### 4. Run the Server
```bash
python -m server.main
```
On first run, tables are created automatically (`Database.initialize()`);
for schema changes going forward, use Alembic (`alembic upgrade head`).

### 5. Access the Editor
Open a web browser and navigate to:
```
http://127.0.0.1:8000
```
Sign up, create a document, and open the same URL in a second browser
window (or share the document with a second account) to test real-time
collaboration.

## Architecture

Loom is built to run as **multiple stateless-ish server instances** behind a
load balancer, coordinated entirely through Redis — no instance ever talks
to another directly.

```
Browser ──REST (JWT)──► FastAPI (auth.py, documents.py) ──► PostgreSQL
   │                                                       (users, documents,
   │                                                        members, operations)
   └──WebSocket (ticket)──► FastAPI /ws (main.py)
                                  │
                    ┌─────────────┼──────────────┐
                    ▼             ▼               ▼
              DocumentOwnership  DocumentRegistry   RedisFanout
              (Redis lock:       (loaded Document   (pub/sub: ops,
               who owns doc X)   objects, lazy-      broadcast, presence
                                 loaded, evicted      + cached doc state)
                                 when idle)
                    │             │               │
                    └─────────────┴───────────────┘
                                  ▼
                                Redis
```

### The core building blocks

1. **OT Engine** (`server/ot_engine.py`) — pure algorithm, no networking or
   DB. Defines `Insert`/`Delete` operations, `apply()`, and `transform()`,
   the function that reconciles two concurrent edits so every client
   converges on the same document regardless of the order operations arrive
   in. Same algorithm family that powers Google Docs.
2. **Document** (`server/document.py`) — wraps the OT engine with state: the
   current text, revision number, and full operation history. When an
   operation arrives at an older revision than the server's current one, the
   history is what lets the server transform it forward before applying it.
3. **DocumentRegistry** (`server/document_registry.py`) — loads `Document`
   objects from Postgres lazily, on first connection, and evicts them from
   memory (after a final flush) once the last client disconnects and a grace
   period elapses — a quick reconnect doesn't pay the reload cost, but an
   abandoned document doesn't sit in memory forever.
4. **DocumentOwnership** (`server/document_ownership.py`) — the key piece
   that makes multi-instance safe. `Document.receive_operation()` (the
   transform-against-history step) must run in exactly one place at a time
   for a given document, or two instances could transform concurrent edits
   against diverging histories. So exactly one instance becomes each
   document's **owner** via a Redis lease (`SET NX EX`, renewed on a
   heartbeat). If the owning instance dies or misses a renewal, the lease
   expires and the next instance to try becomes the new owner.
5. **RedisFanout** (`server/fanout.py`) — pub/sub so every other instance
   can still participate:

   | Channel | Publisher | Subscriber | Purpose |
   |---|---|---|---|
   | `doc-ops:{id}` | a non-owner instance | the owner | forward a client's operation to whoever can process it |
   | `doc-broadcast:{id}` | the owner | every instance with local clients on that doc | the processed result, relayed as `ack` (to the sender) or `operation` (to everyone else) |
   | `doc-control:{id}` | any instance | every instance with local clients on that doc | presence join/leave, `document_deleted` |

   Plus a plain `doc-state:{id}` Redis key (not pub/sub), kept fresh by the
   owner, that non-owner instances use as the sync baseline for a newly
   connecting client — they have no in-memory `Document` of their own to
   read from.

   Deliberately **not** fanned out: cursor position and the full
   cross-instance client list. They're frequent, cosmetic, and don't affect
   document correctness, so they stay local-only per instance rather than
   adding steady Redis traffic.
6. **SessionManager** (`server/session_manager.py`) — tracks which clients
   are connected *to this instance*: their WebSocket objects, cursor
   positions, colors, roles. Purely local; never shared across instances.
7. **Auth** (`server/auth.py`) — JWT access tokens, argon2id-hashed
   passwords, rotated refresh tokens (httpOnly cookie), and single-use
   short-TTL WebSocket "tickets": since browsers can't set custom headers on
   a WS handshake and a long-lived JWT in the query string would leak into
   access logs, a client fetches a ticket over authenticated REST right
   before opening the socket. Tickets live in Redis so a client can fetch a
   ticket from one instance and connect to a different one.
8. **Documents REST API** (`server/documents.py`) — document CRUD and
   sharing. Every document has exactly one owner and zero or more members
   with role `editor` or `viewer`; access (including opening the WebSocket)
   always goes through a `document_members` row — no implicit access, and
   viewers are rejected server-side if they try to send an edit.

### Connecting to a document, end to end

1. Client logs in (`POST /api/auth/login`) → JWT access token.
2. Client requests a WS ticket for one document
   (`POST /api/documents/{id}/ws-ticket`).
3. Client opens `GET /ws?ticket=...`. The server validates the ticket and
   derives `document_id`, identity, and role from it — never from anything
   the client claims directly.
4. This instance tries to become (or confirms it already is) the document's
   owner via `DocumentOwnership`. Owner → loads the `Document` via
   `DocumentRegistry`, that's the sync baseline. Non-owner → reads the
   owner's cached state from Redis instead.
5. Server sends a `sync` message (content, revision, connected clients) and
   tells everyone else the new client joined.
6. As the client edits, it sends `operation` messages. Owning instance →
   applies directly. Non-owner → forwards over `doc-ops`; the result comes
   back over `doc-broadcast`, which this instance is already subscribed to.

For the full rationale — including the two things this deliberately leaves
out of scope (cursor fanout, and the brief unowned gap if a document's
owner loses all its local clients) — see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Frontend (`client/`)

A vanilla-JS single-page app: `ot_client.js` maintains a client-side state
machine for unacknowledged edits (optimistic local apply, reconciled against
server acks/operations), `websocket.js` owns the connection, and
`app.js`/`dashboard.js`/`editor.js` wire everything to the DOM.

## License

MIT License
