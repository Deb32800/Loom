"""
Loom Server — The Main FastAPI Application
============================================

This is where everything connects:
    - OT Engine (transform operations)
    - Document Manager (track document state)
    - Session Manager (track connected clients)
    - WebSocket endpoint (receive and broadcast operations)

The server has two endpoints:
    1. GET /          -> Serves the editor HTML page (Phase 5)
    2. WebSocket /ws  -> Real-time collaboration channel

WHAT HAPPENS WHEN A CLIENT CONNECTS:
    1. Client opens a WebSocket connection to /ws?client_id=xxx
    2. Server accepts the connection (session_manager.connect)
    3. Server sends the current document state (sync message)
    4. Server tells everyone else that a new user joined (presence message)
    5. Client starts sending operations as the user types

WHAT HAPPENS WHEN A CLIENT SENDS AN OPERATION:
    1. Server receives the JSON message via WebSocket
    2. Server parses it into an Insert/Delete operation
    3. Server passes it to document.receive_operation()
       - This transforms the op if the client was behind
       - This applies the (transformed) op to the document
    4. Server sends an 'ack' back to the sender
    5. Server broadcasts the operation to all OTHER clients

MESSAGE TYPES (our protocol):
    Client -> Server:
        { "type": "operation", "revision": 5, "op": {...}, "client_id": "abc" }
        { "type": "cursor",   "position": 42, "client_id": "abc" }

    Server -> Client:
        { "type": "sync",      "content": "...", "revision": 5, "clients": [...] }
        { "type": "ack",       "revision": 6 }
        { "type": "operation", "revision": 6, "op": {...}, "client_id": "abc" }
        { "type": "cursor",    "position": 42, "client_id": "abc", "color": "#E74C3C" }
        { "type": "presence",  "client_id": "abc", "action": "join"/"leave", ... }
"""
from __future__ import annotations
import logging
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from server.ot_engine import Insert, Delete, NoOp
from server.document import Document, OperationEntry
from server.session_manager import SessionManager
from server.database import Database
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('loom.server')
app = FastAPI(title='Loom', description='Real-time collaborative document editor')
document = Document(content='', document_id='main')
session_manager = SessionManager()
db = Database()

@app.on_event('startup')
async def on_startup():
    """Called once when the server starts. Loads document from database."""
    await db.initialize()
    saved = await db.load_document(document.document_id)
    if saved:
        (content, revision) = saved
        document.content = content
        document.revision = revision
        ops = await db.load_operations(document.document_id)
        for op_data in ops:
            document.history.append(OperationEntry(op=op_data['op'], revision=op_data['revision'], client_id=op_data['client_id']))
        logger.info(f'Loaded document: {len(content)} chars, revision {revision}, {len(ops)} operations in history')
    else:
        logger.info('No saved document found, starting fresh')

@app.on_event('shutdown')
async def on_shutdown():
    """Called once when the server stops. Saves document and closes database."""
    await db.save_document(document.document_id, document.content, document.revision)
    await db.close()
    logger.info('Server shutdown complete, document saved')

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
async def websocket_endpoint(websocket: WebSocket, client_id: str=Query(default=None)):
    """The main collaboration WebSocket endpoint.
    
    Each browser tab opens one WebSocket connection to this endpoint.
    The connection stays open for the entire editing session.
    
    Flow:
        1. Client connects with ?client_id=xxx
        2. Server sends current doc state
        3. Server notifies others of the new user
        4. Loop: receive messages, process them, broadcast results
        5. On disconnect: clean up and notify others
    """
    if not client_id:
        client_id = f'user_{uuid.uuid4().hex[:8]}'
    client = await session_manager.connect(client_id, websocket)
    logger.info(f'New connection: {client_id}')
    try:
        await session_manager.send_to(client_id, {'type': 'sync', 'content': document.content, 'revision': document.revision, 'document_id': document.document_id, 'clients': session_manager.get_client_list(), 'your_client_id': client_id, 'your_color': client.color})
        await session_manager.broadcast({'type': 'presence', 'client_id': client_id, 'action': 'join', 'color': client.color}, exclude_client=client_id)
        while True:
            data = await websocket.receive_json()
            await _handle_message(client_id, data)
    except WebSocketDisconnect:
        logger.info(f'Client disconnected: {client_id}')
    except Exception as e:
        logger.error(f'Error with client {client_id}: {e}')
    finally:
        session_manager.disconnect(client_id)
        await session_manager.broadcast({'type': 'presence', 'client_id': client_id, 'action': 'leave'})

async def _handle_message(client_id: str, data: dict) -> None:
    """Route an incoming WebSocket message to the appropriate handler.
    
    Args:
        client_id: Which client sent this message.
        data:      The parsed JSON message.
    """
    msg_type = data.get('type')
    if msg_type == 'operation':
        await _handle_operation(client_id, data)
    elif msg_type == 'cursor':
        await _handle_cursor(client_id, data)
    else:
        logger.warning(f'Unknown message type from {client_id}: {msg_type}')

async def _handle_operation(client_id: str, data: dict) -> None:
    """Process an incoming operation from a client.
    
    This is the core of the collaboration flow:
    1. Parse the operation from JSON
    2. Pass to document.receive_operation() (which transforms if needed)
    3. Send 'ack' to the sender
    4. Broadcast the (possibly transformed) operation to everyone else
    """
    try:
        op = parse_operation(data['op'])
        client_revision = data['revision']
        result = document.receive_operation(op=op, client_revision=client_revision, client_id=client_id)
        if result is None:
            await session_manager.send_to(client_id, {'type': 'ack', 'revision': document.revision})
            return
        await session_manager.send_to(client_id, {'type': 'ack', 'revision': result.revision})
        await session_manager.broadcast({'type': 'operation', 'op': serialize_operation(result.op), 'revision': result.revision, 'client_id': client_id}, exclude_client=client_id)
        await db.save_operation(document.document_id, result.op, result.revision, client_id)
        await db.save_document(document.document_id, document.content, document.revision)
        logger.info(f'Op from {client_id}: {serialize_operation(result.op)} -> rev {result.revision}')
    except (ValueError, KeyError) as e:
        logger.error(f'Invalid operation from {client_id}: {e}')
        await session_manager.send_to(client_id, {'type': 'error', 'message': str(e)})

async def _handle_cursor(client_id: str, data: dict) -> None:
    """Process a cursor position update from a client.
    
    When a user moves their cursor (clicks, arrow keys, etc.),
    the client sends the new position. We broadcast it to everyone
    else so they can show a colored cursor at that position.
    """
    position = data.get('position', 0)
    session_manager.update_cursor(client_id, position)
    client_list = session_manager.get_client_list()
    client_info = next((c for c in client_list if c['client_id'] == client_id), None)
    color = client_info['color'] if client_info else '#999'
    await session_manager.broadcast({'type': 'cursor', 'client_id': client_id, 'position': position, 'color': color}, exclude_client=client_id)
import os
CLIENT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'client')
if os.path.isdir(CLIENT_DIR):
    app.mount('/static', StaticFiles(directory=CLIENT_DIR), name='static')

    @app.get('/')
    async def serve_editor():
        """Serve the main editor page."""
        index_path = os.path.join(CLIENT_DIR, 'index.html')
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {'message': 'Loom server is running. Frontend not yet built.'}
else:

    @app.get('/')
    async def root():
        """Health check endpoint until the frontend is built."""
        return {'app': 'Loom', 'status': 'running', 'document_revision': document.revision, 'connected_clients': session_manager.client_count}
if __name__ == '__main__':
    import uvicorn
    uvicorn.run('server.main:app', host='0.0.0.0', port=8000, reload=True, log_level='info')