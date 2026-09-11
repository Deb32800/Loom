"""
Loom Server — The Main FastAPI Application
============================================

This is where everything connects:
    - OT Engine (transform operations)
    - DocumentRegistry (load/cache/evict per-document state)
    - DocumentOwnership (single-writer-per-document Redis lock)
    - RedisFanout (cross-instance ops-forwarding + broadcast + presence)
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
    4. This instance tries to become (or confirms it already is) the
       document's owner via a Redis lock. If it is, it loads the document
       (DocumentRegistry) and that's its sync baseline. If some other
       instance already owns it, this instance reads the owner's live
       state from a Redis-cached snapshot instead — it never loads its own
       Document object for a document it doesn't own.
    5. Server tells everyone else on that document that a new user joined
    6. Client starts sending operations as the user types (rejected
       server-side if their role is 'viewer'). If this instance owns the
       document, it processes the operation directly; if not, it forwards
       it to the owner over Redis and the owner's result comes back
       through the same broadcast channel this instance is already
       subscribed to.

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
from server.ot_engine import NoOp
from server.wire_format import parse_operation, serialize_operation
from server.document_registry import DocumentRegistry
from server.document_ownership import DocumentOwnership
from server.fanout import RedisFanout
from server.session_manager import SessionManager
from server.database import Database
from server.redis_client import create_redis_client
from server.auth import auth_router, consume_ws_ticket
from server.documents import documents_router
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('loom.server')
app = FastAPI(title='Loom', description='Real-time collaborative document editor')
app.include_router(auth_router)
app.include_router(documents_router)

session_manager = SessionManager()
db = Database()
redis_client = create_redis_client()

async def _on_ownership_lost(document_id: str) -> None:
    """DocumentOwnership calls this when a lease is lost involuntarily (the
    renew loop couldn't renew it in time, e.g. a GC pause) rather than via
    an explicit release(). Stop listening for forwarded ops we're no longer
    eligible to process — same cleanup _on_document_evicted does for the
    voluntary path below, just triggered from the other direction."""
    await fanout.release_ops_subscription(document_id)

ownership = DocumentOwnership(redis_client, on_lost=_on_ownership_lost)

async def _on_document_evicted(document_id: str) -> None:
    """DocumentRegistry calls this when a document leaves memory (grace
    period elapsed, or an explicit evict on delete). We were only ever
    eligible to have it loaded if we owned it, so give up that lock too —
    and stop listening for forwarded ops nobody will process anymore."""
    await ownership.release(document_id)
    await fanout.release_ops_subscription(document_id)

registry = DocumentRegistry(db, on_evict=_on_document_evicted)

pending_db_ops = []

async def _process_and_publish(document_id: str, client_id: str, op, client_revision: int) -> None:
    """Apply an operation to a document THIS instance owns — whether it
    came from one of our own local clients or was forwarded to us by
    another instance over doc-ops — and publish the result. Every instance
    with local clients on this document (including us) relays that
    publish as an 'ack' to the sender or an 'operation' broadcast to
    everyone else, via RedisFanout's doc-broadcast subscription."""
    document = registry.get_active(document_id)
    if document is None:
        await session_manager.send_to(document_id, client_id, {'type': 'error', 'message': 'Document not currently loaded'})
        return
    try:
        result = document.receive_operation(op=op, client_revision=client_revision, client_id=client_id)
    except ValueError as e:
        await session_manager.send_to(document_id, client_id, {'type': 'error', 'message': str(e)})
        return
    if result is None:
        # The op fully resolved to a no-op after transform — just ack the
        # sender at the current revision, nothing to broadcast to anyone else.
        await fanout.publish_broadcast(document_id, serialize_operation(NoOp()), document.revision, client_id, noop=True)
        return
    await fanout.set_cached_state(document_id, document.content, document.revision)
    await fanout.publish_broadcast(document_id, serialize_operation(result.op), result.revision, client_id, noop=False)
    pending_db_ops.append((document_id, result.op, result.revision, client_id))
    logger.info(f'Op from {client_id} on {document_id}: {serialize_operation(result.op)} -> rev {result.revision}')

async def _on_forwarded_operation(document_id: str, data: dict) -> None:
    """Called when a doc-ops message arrives — only instances currently
    subscribed (i.e. the owner) ever receive these."""
    if not ownership.is_owner(document_id):
        # Rare race: message was in flight as we released ownership. Drop
        # it rather than process it incorrectly — see fanout.py's docstring
        # for why this narrow gap is an accepted scope boundary, not a bug.
        return
    try:
        op = parse_operation(data['op'])
    except (ValueError, KeyError) as e:
        logger.error(f'Bad forwarded operation for {document_id}: {e}')
        return
    await _process_and_publish(document_id, data['client_id'], op, data['revision'])

fanout = RedisFanout(redis_client, session_manager, on_forwarded_operation=_on_forwarded_operation)

app.state.db = db
app.state.registry = registry
app.state.session_manager = session_manager
app.state.redis = redis_client
app.state.ownership = ownership
app.state.fanout = fanout

# Background task for batching DB writes
db_flush_task = None

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
    await redis_client.ping()  # fail fast at startup if Redis is unreachable
    logger.info('Redis connection established')
    await fanout.start()
    global db_flush_task
    db_flush_task = asyncio.create_task(db_flush_loop())

@app.on_event('shutdown')
async def on_shutdown():
    """Called once when the server stops. Flushes every active document,
    releases every ownership lock this instance held (so another instance
    can take over immediately instead of waiting out the lease TTL), and
    closes the database/Redis connections."""
    if db_flush_task:
        db_flush_task.cancel()

    if pending_db_ops:
        for op_args in pending_db_ops:
            await db.save_operation(*op_args)

    await registry.flush_all()
    await ownership.release_all()
    await fanout.stop()
    await db.close()
    await redis_client.aclose()
    logger.info('Server shutdown complete, all active documents saved')

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
        2. This instance tries to become the document's owner (or confirms
           it already is). Owner: loads the document via DocumentRegistry.
           Non-owner: reads the owner's live state from Redis instead.
        3. Server notifies others on the same document of the new user
        4. Loop: receive messages, process them (directly if we own the
           document, forwarded to the owner over Redis if not)
        5. On disconnect: clean up and notify others
    """
    ticket_info = await consume_ws_ticket(redis_client, ticket)
    if ticket_info is None:
        await websocket.close(code=4401)
        return
    document_id = ticket_info.document_id
    role = ticket_info.role

    is_owner = await ownership.try_acquire(document_id)
    if is_owner:
        try:
            document = await registry.acquire(document_id)
        except KeyError:
            await ownership.release(document_id)
            await websocket.close(code=4404)
            return
        await fanout.set_cached_state(document_id, document.content, document.revision)
        await fanout.ensure_ops_subscription(document_id)
        sync_content, sync_revision = document.content, document.revision
    else:
        cached = await fanout.get_cached_state(document_id)
        if cached is not None:
            sync_content, sync_revision = cached
        else:
            saved = await db.load_document(document_id)
            if saved is None:
                await websocket.close(code=4404)
                return
            sync_content, sync_revision = saved

    await fanout.ensure_document_subscription(document_id)

    # Suffix so the same user connecting from multiple tabs gets distinct
    # session identities instead of silently stomping on each other in
    # session_manager's client_id-keyed dict.
    client_id = f'{ticket_info.username}-{secrets.token_hex(3)}'
    client = await session_manager.connect(document_id, client_id, websocket, role)
    logger.info(f"New connection: {client_id} -> document {document_id} ({role}, {'owner' if is_owner else 'follower'} instance)")
    try:
        await session_manager.send_to(document_id, client_id, {'type': 'sync', 'content': sync_content, 'revision': sync_revision, 'document_id': document_id, 'title': ticket_info.title, 'clients': session_manager.get_client_list(document_id), 'your_client_id': client_id, 'your_color': client.color, 'your_role': role})
        await fanout.publish_control(document_id, {'type': 'presence', 'client_id': client_id, 'action': 'join', 'color': client.color})
        while True:
            data = await websocket.receive_json()
            await _handle_message(document_id, client_id, role, data)
    except WebSocketDisconnect:
        logger.info(f'Client disconnected: {client_id}')
    except Exception as e:
        logger.error(f'Error with client {client_id}: {e}')
    finally:
        session_manager.disconnect(document_id, client_id)
        await fanout.release_document_subscription(document_id)
        if is_owner:
            registry.release(document_id)
        await fanout.publish_control(document_id, {'type': 'presence', 'client_id': client_id, 'action': 'leave'})

async def _handle_message(document_id: str, client_id: str, role: str, data: dict) -> None:
    """Route an incoming WebSocket message to the appropriate handler.

    Args:
        document_id: The document this connection is scoped to.
        client_id:   Which client sent this message.
        role:        The sender's role on this document.
        data:        The parsed JSON message.
    """
    msg_type = data.get('type')
    if msg_type == 'operation':
        await _handle_operation(document_id, client_id, role, data)
    elif msg_type == 'cursor':
        await _handle_cursor(document_id, client_id, data)
    else:
        logger.warning(f'Unknown message type from {client_id}: {msg_type}')

async def _handle_operation(document_id: str, client_id: str, role: str, data: dict) -> None:
    """Process an incoming operation from a client.

    1. Reject if the sender is a read-only viewer
    2. Parse the operation from JSON
    3. If this instance owns the document, process it directly
       (document.receive_operation transforms if needed) and publish the
       result. If not, forward it to whichever instance does — their
       result reaches us (and gets relayed to this client) through the
       same doc-broadcast subscription every instance with local clients
       on this document maintains.
    """
    if role == 'viewer':
        await session_manager.send_to(document_id, client_id, {'type': 'error', 'message': 'You have read-only access to this document'})
        return
    try:
        op = parse_operation(data['op'])
        client_revision = data['revision']
    except (ValueError, KeyError) as e:
        logger.error(f'Invalid operation from {client_id}: {e}')
        await session_manager.send_to(document_id, client_id, {'type': 'error', 'message': str(e)})
        return

    if ownership.is_owner(document_id):
        await _process_and_publish(document_id, client_id, op, client_revision)
    else:
        await fanout.forward_operation(document_id, data['op'], client_revision, client_id)

async def _handle_cursor(document_id: str, client_id: str, data: dict) -> None:
    """Process a cursor position update from a client.

    Cursor position is intentionally local-only (not fanned out across
    instances, unlike operations/ownership/presence) — see fanout.py's
    docstring for why: frequent, cosmetic, and doesn't affect document
    correctness, so it's not worth the steady cross-instance Redis traffic.
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
