# Loom

**A real-time collaborative document editor, built from the algorithm up.**

Loom isn't "Google Docs with a library installed" — the Operational
Transformation engine that reconciles concurrent edits is hand-written
(`server/ot_engine.py`), and everything around it — auth, multi-document
sharing, and a Redis-coordinated backend that can run as more than one
server at once — was built to support that engine properly rather than
bolt collaboration onto an existing text editor.

## Live

- **Azure (Australia East):** https://loom-backend-az.azurewebsites.net
- **Render (Singapore):** https://loom-fx82.onrender.com

Yes, there are two. That's not an accident — see [Performance](#performance-what-actually-happened-when-we-measured-it)
below for why keeping both around turned out to be the more honest choice.
Sign up, create a document, and open it in a second tab (or share it with a
second account) to see the real-time sync.

---

## What it actually does

- **Operational Transformation** — concurrent edits from multiple people
  converge to the same document, in any arrival order. Same algorithm
  family as Google Docs, written from scratch and unit-tested.
- **Multi-document workspace** — create, rename, delete, and share
  documents, with `owner` / `editor` / `viewer` roles enforced server-side.
- **Real JWT auth** — argon2id password hashing, short-lived access
  tokens, rotated refresh tokens, single-use WebSocket tickets so a raw
  JWT never has to sit in a URL.
- **Actually multi-instance** — document ownership and cross-instance
  broadcast run through Redis, so this isn't a single-process toy; it's
  designed to run as more than one server behind a load balancer with no
  sticky sessions. Full writeup in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- **PostgreSQL persistence** via SQLAlchemy's async ORM, schema owned by
  Alembic.

## Stack

- **Backend:** Python 3.9+, FastAPI, Uvicorn
- **Database:** PostgreSQL (Neon, serverless Postgres) via SQLAlchemy async + asyncpg
- **Coordination:** Redis (Upstash) — WS-ticket store, document ownership locks, pub/sub fanout
- **Auth:** JWT access tokens, argon2id password hashing
- **Frontend:** vanilla HTML/CSS/JS — no framework, on purpose, so nothing hides what the OT client is actually doing
- **Deployed on:** both Azure App Service (Container) and Render, same Docker image, same code

## Project Structure

```
Loom/
├── server/
│   ├── ot_engine.py          # OT operations & transform functions — the actual algorithm
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
│   └── ARCHITECTURE.md       # The deep dive — how the multi-instance design actually works
├── scripts/
│   ├── loadtest.py           # Concurrency/latency load tester (used for the numbers below)
│   └── repro_fanout.py       # Isolates the Redis fanout logic against any Redis URL
├── tests/
│   ├── test_ot_engine.py     # OT correctness tests
│   └── test_document.py      # Document state tests
├── docker-compose.yml        # Local Postgres + Redis
├── requirements.txt
└── README.md
```

## Running it yourself

```bash
git clone https://github.com/Deb32800/Loom.git
cd Loom
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
docker compose up -d          # local Postgres on 5432, Redis on 6379
python -m server.main
```

Then open `http://127.0.0.1:8000`, sign up, create a document, and open a
second tab to try real-time collaboration. Tables are created automatically
on first run; for schema changes after that, use `alembic upgrade head`.

---

## Performance: what actually happened when we measured it

I didn't want a "trust me, it's fast" README, so here's what was actually
tested, against the real live deployments, with the real Neon Postgres and
Upstash Redis behind them — not a local mock of anything.

### The Azure-vs-Render lesson

The instinct was: put the server in the same region as the database
(Neon + Upstash are both in Sydney, `ap-southeast-2`) and everything gets
faster. So the backend got redeployed to Azure App Service in **Australia
East** — same metro as the database. Then it got measured head-to-head
against the existing Render deployment in Singapore, same test, same
document, same 10 sequential edits, run from the same machine:

| | avg | min | max |
|---|---|---|---|
| Render — Singapore | **367ms** | 270ms | 637ms |
| Azure — Australia East | **495ms** | 380ms | 678ms |

Azure — the one colocated with the database — was **slower**. Turns out
"put the server near the database" only helps if your *users* are also
near that database. The test machine is in Japan; Japan→Singapore is a
shorter hop than Japan→Sydney, and that client-to-server leg mattered more
than the now-nearly-free server-to-database leg. Optimizing one hop of a
three-hop trip (user → server → database) without checking where the
*user* actually is just moved the bottleneck around.

Both deployments are kept live because of this — Render is faster for the
actual person testing it right now, Azure is the more resume-relevant
piece of infrastructure work and would win for users based in Australia.
Neither answer is "wrong"; it depends who's using it, which is the actual
point.

### How much concurrency it can take

Using `scripts/loadtest.py` against the real Postgres/Redis, ramping up
how many people can edit **the same single document** at once (the
realistic worst case, since Loom deliberately routes each document through
one owning instance — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for why):

| Concurrent editors on one doc | Connected | Avg ack | p95 ack |
|---|---|---|---|
| 5 | 5/5 (100%) | 226ms | 355ms |
| 15 | 15/15 (100%) | 284ms | 587ms |
| 30 | 30/30 (100%) | 489ms | 1.5s |
| 60 | 59/60 (98%) | 848ms | 1.9s |
| 100 | 97/100 (97%) | 925ms | 1.8s |

Even at **100 people typing into the exact same document at once** — a
scenario more extreme than most real products ever hit, and one Google
Docs itself caps out well before — connections stayed reliable (97-100%)
and the average round-trip for an edit to be acknowledged stayed under a
second. That held up on a single free-tier compute instance with every
operation round-tripping to a database and a cache both a continent away.
For the realistic case — 1000s of users spread across many documents, a
handful of people per document — the architecture scales by adding more
server instances rather than needing any of this to get faster, because
ownership and fanout were built for exactly that from the start (again,
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) has the why).

*(This exercise also caught a real bug, not just numbers — a Redis pub/sub
subscription that silently died at server startup and dropped every
acknowledgment, invisible until it was actually load-tested. Fixed in
`server/fanout.py`; the story's worth asking about if you're reading this
for an interview.)*

## Architecture, briefly

Loom runs as **multiple server instances** behind a load balancer,
coordinated entirely through Redis — no instance ever talks to another
directly.

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

The one idea worth understanding before anything else: `Document.receive_operation()`
— the actual transform-against-history step — has to run in exactly one
place at a time for a given document, or two servers could transform
concurrent edits against different histories and the document would
diverge. So exactly one instance becomes each document's **owner** at a
time (a Redis lease, renewed on a heartbeat, that expires on its own if
that instance dies). Every other instance just forwards operations to
whichever instance owns the document, and relays the result back to its
own locally-connected users over pub/sub. That's the entire trick that
makes this horizontally scalable without a distributed version of OT,
which nobody actually builds in production.

Everything else — the auth flow, the WebSocket ticket handshake, exactly
which three Redis channels exist and why, what's deliberately kept
local-only (cursor positions) — is written up properly in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). This section is the
five-minute version; that doc is the real one.

## License

MIT License
