# 🧵 Loom

**Real-time collaborative document editor** 

> "Weaving edits together, in real time."

---

## 🎯 What is Loom?

Loom is a collaborative text editor where **multiple users can edit the same document simultaneously**, with changes appearing in real-time across all connected clients. Think Google Docs, but built from scratch using Python.

The core conflict resolution uses **Operational Transformation (OT)** — the same algorithm family that powers Google Docs and Etherpad.

## 🧠 What You'll Learn By Building This

| Concept | Why It Matters |
|---------|---------------|
| **Operational Transformation** | How to resolve conflicting edits from multiple users |
| **WebSockets** | Real-time bidirectional communication (not request-response) |
| **Async Python** | Non-blocking I/O for handling many connections |
| **State Machines** | Client-side OT uses a 3-state machine for buffering |
| **Distributed Systems** | Consistency, ordering, and the CAP theorem in practice |

## 🛠️ Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| **Backend** | Python 3.12+ / FastAPI | Modern async framework with native WebSocket support |
| **OT Engine** | Custom (pure Python) | Built from scratch to learn the algorithm deeply |
| **Database** | SQLite (aiosqlite) | Zero setup, perfect for MVP |
| **Frontend** | Vanilla HTML/CSS/JS | No framework overhead — focus on core logic |
| **Editor** | CodeMirror 6 | Production-grade editor with collaboration support |

## 📁 Project Structure

```
Loom/
├── server/                   # Python backend
│   ├── ot_engine.py          # OT operations & transform functions
│   ├── document.py           # Document state management
│   ├── session_manager.py    # Connected clients & sessions
│   ├── database.py           # SQLite persistence
│   └── main.py               # FastAPI app & WebSocket endpoint
├── client/                   # Frontend
│   ├── index.html            # Editor page
│   ├── css/
│   │   └── editor.css        # Editor styling
│   └── js/
│       ├── app.js            # Main application wiring
│       ├── websocket.js      # WebSocket connection handling
│       ├── ot_client.js      # Client-side OT state machine
│       └── editor.js         # CodeMirror integration
├── tests/                    # Test suite
│   ├── test_ot_engine.py     # OT correctness tests
│   ├── test_document.py      # Document manager tests
│   └── test_integration.py   # End-to-end tests
├── docs/                     # Learning documentation
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/Deb32800/Loom.git
cd Loom

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the server
python -m server.main

# 5. Open two browser tabs at http://localhost:8000
#    Start typing in both — watch the magic! ✨
```

## 🏗️ Build Order (Why This Sequence?)

We build bottom-up — **hardest, most isolated piece first**:

```
Phase 1: OT Engine         ← Pure algorithm, no I/O, fully testable
Phase 2: Document Manager   ← Uses OT engine, manages state
Phase 3: WebSocket Server   ← Wires OT to the network
Phase 4: Database            ← Adds persistence
Phase 5: Frontend Editor     ← Visual layer on top
```

Each phase is independently testable before adding the next layer.

## 🔑 The Core Idea: Operational Transformation

When two users edit simultaneously:

```
Document: "Hello World"

User A inserts "Beautiful " at position 6  →  "Hello Beautiful World"
User B deletes "World" at position 6       →  "Hello "

OT transforms B's operation:
  "Delete at position 6" becomes "Delete at position 16"
  Because A inserted 10 characters before that position

Result: "Hello Beautiful " ✅ (Both intentions preserved)
```

## 📄 License

MIT

## 🤝 Contributing

This is a learning project. PRs, issues, and discussions are welcome!
