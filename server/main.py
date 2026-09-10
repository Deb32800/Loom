"""
Loom Server — The Main FastAPI Application
============================================

This is where everything connects:
    - OT Engine (transform operations)
    - DocumentRegistry (load/cache/evict per-document state)
    - Session Manager (track connected clients, scoped per document)
    - WebSocket endpoint (receive and broadcast operations)
    - Auth + Documents REST routers (server/auth.py, server/documents.py)

WHAT HAPPENS WHEN A CLIENT CONNECTS:
    1. Client authenticates over REST (POST /api/auth/login) and fetches a
       single-use WS ticket for one specific document
       (POST /api/documents/{id}/ws-ticket)
    2. Client opens a WebSocket connection to /ws?ticket=xxx
    3. Server validates + consumes the ticket, deriving document_id, the
       caller's identity, and their role (owner/editor/viewer) from it —
       never from anything the client claims directly
    4. Server loads the document (DocumentRegistry, cached after first load)
       and sends its current state (sync message)
    5. Server tells everyone else on that document that a new user joined
    6. Client starts sending operations as the user types (rejected
       server-side if their role is 'viewer')

MESSAGE TYPES (our protocol):
    Client -> Server:
        { "type": "operation", "revision": 5, "op": {...} }
        { "type": "cursor",   "position": 42 }

    Server -> Client:
        { "type": "sync",      "content": "...", "revision": 5, "clients": [...] }
        { "type": "ack",       "revision": 6 }
        { "type": "operation", "revision": 6, "op": {...}, "client_id": "abc" }
        { "type": "cursor",    "position": 42, "client_id": "abc", "color": "#E74C3C" }
        { "type": "presence",  "client_id": "abc", "action": "join"/"leave", ... }
        { "type": "document_deleted" }
        { "type": "error",     "message": "..." }
"""
from __future__ import annotations
import logging
import secrets
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from server.ot_engine import Insert, Delete, NoOp
from server.document import Document
from server.document_registry import DocumentRegistry
from server.session_manager import SessionManager
from server.database import Database
from server.auth import auth_router, consume_ws_ticket
from server.documents import documents_router
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('loom.server')
app = FastAPI(title='Loom', description='Real-time collaborative document editor')
app.include_router(auth_router)
app.include_router(documents_router)
session_manager = SessionManager()
db = Database()
registry = DocumentRegistry(db)
app.state.db = db
app.state.registry = registry
app.state.session_manager = session_manager

# Background task for batching DB writes
db_flush_task = None
pending_db_ops = []

async def db_flush_loop():
    """Periodically flush operations and touched documents' state to the database."""
    while True:
        await asyncio.sleep(2.0)
        try:
            if pending_db_ops:
                ops_to_flush = pending_db_ops[:]
                pending_db_ops.clear()
                touched_document_ids = set()
                for op_args in ops_to_flush:
                    await db.save_operation(*op_args)
                    touched_document_ids.add(op_args[0])
                for document_id in touched_document_ids:
                    active_doc = registry.get_active(document_id)
                    if active_doc is not None:
                        await db.save_document(document_id, active_doc.content, active_doc.revision)
                logger.debug(f'Flushed {len(ops_to_flush)} operations across {len(touched_document_ids)} documents')
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f'Error in DB flush loop: {e}')

@app.on_event('startup')
async def on_startup():
    """Called once when the server starts. Individual documents are loaded
    lazily on first connection (DocumentRegistry), not eagerly here."""
    await db.initialize()
    global db_flush_task
    db_flush_task = asyncio.create_task(db_flush_loop())

@app.on_event('shutdown')
async def on_shutdown():
    """Called once when the server stops. Flushes every active document and
    closes the database."""
    if db_flush_task:
        db_flush_task.cancel()

    if pending_db_ops:
        for op_args in pending_db_ops:
            await db.save_operation(*op_args)

    await registry.flush_all()
    await db.close()
    logger.info('Server shutdown complete, all active documents saved')

def parse_operation(op_data: dict):
    """Convert a JSON operation dict into an Insert or Delete object.

    Expected format:
        {"type": "insert", "position": 5, "text": "hello"}
        {"type": "delete", "position": 5, "count": 3}

    Args:
        op_data: Dictionary from the client's JSON message.

    Returns:
        An Insert or Delete operation.

    Raises:
        ValueError: If the operation format is invalid.
    """
    op_type = op_data.get('type')
    if op_type == 'insert':
        return Insert(position=op_data['position'], text=op_data['text'])
    elif op_type == 'delete':
        return Delete(position=op_data['position'], count=op_data['count'])
    else:
        raise ValueError(f'Unknown operation type: {op_type}')

def serialize_operation(op) -> dict:
    """Convert an Insert/Delete/NoOp into a JSON-serializable dict.

    This is the reverse of parse_operation. We need this to send
    operations back to clients over WebSocket.
    """
    if isinstance(op, Insert):
        return {'type': 'insert', 'position': op.position, 'text': op.text}
    elif isinstance(op, Delete):
        return {'type': 'delete', 'position': op.position, 'count': op.count}
    elif isinstance(op, NoOp):
        return {'type': 'noop'}
    else:
        raise ValueError(f'Cannot serialize operation: {type(op)}')

@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket, ticket: str=Query(...)):
    """The main collaboration WebSocket endpoint.

    Each browser tab opens one WebSocket connection to this endpoint, scoped
    to exactly one document. The connection stays open for the entire
    editing session.

    Flow:
        1. Client fetches a single-use ticket from
           POST /api/documents/{document_id}/ws-ticket (authenticated via a
           normal JWT bearer token) and connects with ?ticket=xxx —
           browsers can't set custom headers on a WS handshake, and a
           long-lived JWT in the query string would end up in access logs,
           so a short-lived one-time ticket stands in for it. The ticket
           carries document_id, the caller's identity, and their role.
        2. Server loads the document (DocumentRegistry) and sends its
           current state (sync message)
        3. Server notifies others on the same document of the new user
        4. Loop: receive messages, process them, broadcast results
        5. On disconnect: clean up and notify others
    """
    ticket_info = consume_ws_ticket(ticket)
    if ticket_info is None:
        await websocket.close(code=4401)
        return
    document_id = ticket_info.document_id
    role = ticket_info.role
    try:
        document = await registry.acquire(document_id)
    except KeyError:
        await websocket.close(code=4404)
        return

    # Suffix so the same user connecting from multiple tabs gets distinct
    # session identities instead of silently stomping on each other in
    # session_manager's client_id-keyed dict.
    client_id = f'{ticket_info.username}-{secrets.token_hex(3)}'
    client = await session_manager.connect(document_id, client_id, websocket, role)
    logger.info(f'New connection: {client_id} -> document {document_id} ({role})')
    try:
        await session_manager.send_to(document_id, client_id, {'type': 'sync', 'content': document.content, 'revision': document.revision, 'document_id': document_id, 'title': ticket_info.title, 'clients': session_manager.get_client_list(document_id), 'your_client_id': client_id, 'your_color': client.color, 'your_role': role})
        await session_manager.broadcast(document_id, {'type': 'presence', 'client_id': client_id, 'action': 'join', 'color': client.color}, exclude_client=client_id)
        while True:
            data = await websocket.receive_json()
            await _handle_message(document, document_id, client_id, role, data)
    except WebSocketDisconnect:
        logger.info(f'Client disconnected: {client_id}')
    except Exception as e:
        logger.error(f'Error with client {client_id}: {e}')
    finally:
        session_manager.disconnect(document_id, client_id)
        registry.release(document_id)
        await session_manager.broadcast(document_id, {'type': 'presence', 'client_id': client_id, 'action': 'leave'})

async def _handle_message(document: Document, document_id: str, client_id: str, role: str, data: dict) -> None:
    """Route an incoming WebSocket message to the appropriate handler.

    Args:
        document:    The document this connection is scoped to.
        document_id: That document's id.
        client_id:   Which client sent this message.
        role:        The sender's role on this document.
        data:        The parsed JSON message.
    """
    msg_type = data.get('type')
    if msg_type == 'operation':
        await _handle_operation(document, document_id, client_id, role, data)
    elif msg_type == 'cursor':
        await _handle_cursor(document_id, client_id, data)
    else:
        logger.warning(f'Unknown message type from {client_id}: {msg_type}')

async def _handle_operation(document: Document, document_id: str, client_id: str, role: str, data: dict) -> None:
    """Process an incoming operation from a client.

    This is the core of the collaboration flow:
    1. Reject if the sender is a read-only viewer
    2. Parse the operation from JSON
    3. Pass to document.receive_operation() (which transforms if needed)
    4. Send 'ack' to the sender
    5. Broadcast the (possibly transformed) operation to everyone else
    """
    if role == 'viewer':
        await session_manager.send_to(document_id, client_id, {'type': 'error', 'message': 'You have read-only access to this document'})
        return
    try:
        op = parse_operation(data['op'])
        client_revision = data['revision']
        result = document.receive_operation(op=op, client_revision=client_revision, client_id=client_id)
        if result is None:
            await session_manager.send_to(document_id, client_id, {'type': 'ack', 'revision': document.revision})
            return
        await session_manager.send_to(document_id, client_id, {'type': 'ack', 'revision': result.revision})
        await session_manager.broadcast(document_id, {'type': 'operation', 'op': serialize_operation(result.op), 'revision': result.revision, 'client_id': client_id}, exclude_client=client_id)

        # Batch DB saves
        pending_db_ops.append((document_id, result.op, result.revision, client_id))

        logger.info(f'Op from {client_id} on {document_id}: {serialize_operation(result.op)} -> rev {result.revision}')
    except (ValueError, KeyError) as e:
        logger.error(f'Invalid operation from {client_id}: {e}')
        await session_manager.send_to(document_id, client_id, {'type': 'error', 'message': str(e)})

async def _handle_cursor(document_id: str, client_id: str, data: dict) -> None:
    """Process a cursor position update from a client.

    When a user moves their cursor (clicks, arrow keys, etc.),
    the client sends the new position. We broadcast it to everyone
    else on the same document so they can show a colored cursor there.
    """
    position = data.get('position', 0)
    session_manager.update_cursor(document_id, client_id, position)
    client_list = session_manager.get_client_list(document_id)
    client_info = next((c for c in client_list if c['client_id'] == client_id), None)
    color = client_info['color'] if client_info else '#999'
    await session_manager.broadcast(document_id, {'type': 'cursor', 'client_id': client_id, 'position': position, 'color': color}, exclude_client=client_id)
import os
CLIENT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'client')
if os.path.isdir(CLIENT_DIR):
    app.mount('/static', StaticFiles(directory=CLIENT_DIR), name='static')

    @app.get('/')
    async def serve_editor():
        """Serve the main app page (login/dashboard/editor — a single-page app)."""
        index_path = os.path.join(CLIENT_DIR, 'index.html')
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {'message': 'Loom server is running. Frontend not yet built.'}
else:

    @app.get('/')
    async def root():
        """Health check endpoint until the frontend is built."""
        return {'app': 'Loom', 'status': 'running', 'connected_clients': session_manager.total_client_count}
if __name__ == '__main__':
    import uvicorn
    uvicorn.run('server.main:app', host='0.0.0.0', port=8000, reload=True, log_level='info')
