# Loom

**Real-Time Collaborative Document Editor**

Loom is a collaborative text editor where multiple users can edit the same document simultaneously, with changes appearing in real-time across all connected clients. It leverages Operational Transformation (OT) for robust conflict resolution.

---

## Overview

Loom provides a backend Operational Transformation engine and a real-time WebSocket communication layer to handle concurrent edits seamlessly. 

### Key Features
- **Operational Transformation**: Accurate resolution of concurrent edits.
- **Real-Time Synchronization**: Low-latency updates using WebSockets.
- **Data Persistence**: Asynchronous SQLite integration (aiosqlite) with Write-Ahead Logging for high concurrency.
- **Client-Side State Machine**: Robust handling of network delays and unacknowledged operations.

## Technology Stack

- **Backend**: Python 3.9+, FastAPI, Uvicorn
- **Persistence**: SQLite (aiosqlite)
- **Frontend**: HTML5, CSS3, Vanilla JavaScript

## Project Structure

```
Loom/
├── server/
│   ├── ot_engine.py          # OT operations & transform functions
│   ├── document.py           # Document state management
│   ├── session_manager.py    # Connected clients & sessions
│   ├── database.py           # SQLite persistence layer
│   └── main.py               # FastAPI application & WebSocket endpoint
├── client/
│   ├── index.html            # Editor interface
│   ├── css/
│   │   └── editor.css        # UI styling
│   └── js/
│       ├── app.js            # Application wiring
│       ├── websocket.js      # WebSocket client connection manager
│       ├── ot_client.js      # OT state machine logic
│       └── editor.js         # Textarea input event handler
├── tests/
│   ├── test_ot_engine.py     # OT correctness tests
│   └── test_document.py      # Document state tests
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
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the Server
```bash
python -m server.main
```

### 5. Access the Editor
Open a web browser and navigate to:
```
http://127.0.0.1:8000
```
Open the same URL in a second browser window to test real-time collaboration.

## Architecture and Design

The development of Loom follows a bottom-up architectural approach:

1. **OT Engine**: Implements `Insert` and `Delete` operations alongside the `transform()` function to ensure eventual consistency.
2. **Document Manager**: Maintains the document state, revision history, and applies transformations for operations received out of order.
3. **WebSocket Server**: Handles client connections, dispatches incoming operations, and broadcasts state changes.
4. **Database Layer**: Persists the document content and complete operation history to SQLite.
5. **Frontend**: Interfaces with the user, converting input events into discrete operations and maintaining an internal state machine to manage unacknowledged edits.

## License

MIT License
