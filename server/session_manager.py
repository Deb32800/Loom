"""
Loom Session Manager — Tracking Connected Clients Per Document
=================================================================

This module manages WebSocket connections, scoped per document: it knows
which clients are connected to which document, each client's WebSocket
object (so we can send them messages), each client's cursor position, and
each client's role on that document (owner/editor/viewer — viewer is
read-only, enforced by the caller before an operation is applied).

It does NOT handle OT or document state — that's the Document/DocumentRegistry's
job. This module is purely connection management and message broadcasting,
scoped so that edits/cursors/presence in one document never leak into another.
"""
from __future__ import annotations
import logging
from typing import Dict, Optional
from fastapi import WebSocket
logger = logging.getLogger('loom.session')

class ClientInfo:
    """Information about a single connected client within one document.

    Attributes:
        client_id:  Unique identifier for this connection (e.g. "alice-be6aed").
        websocket:  The WebSocket connection object.
        cursor_pos: The client's last known cursor position in the document.
        color:      A color assigned to this client for their remote cursor.
        role:       This client's access role on the document ('owner' /
                    'editor' / 'viewer').
    """
    CURSOR_COLORS = ['#E74C3C', '#3498DB', '#2ECC71', '#F39C12', '#9B59B6', '#1ABC9C', '#E67E22', '#E91E63']
    _color_index = 0

    def __init__(self, client_id: str, websocket: WebSocket, role: str):
        self.client_id = client_id
        self.websocket = websocket
        self.role = role
        self.cursor_pos: int = 0
        self.color = ClientInfo.CURSOR_COLORS[ClientInfo._color_index % len(ClientInfo.CURSOR_COLORS)]
        ClientInfo._color_index += 1

class SessionManager:
    """Manages all connected WebSocket clients, grouped by document_id.

    Usage:
        manager = SessionManager()
        await manager.connect(document_id, client_id, websocket, role="editor")
        await manager.broadcast(document_id, message_dict, exclude_client="alice-be6aed")
        manager.disconnect(document_id, client_id)
    """

    def __init__(self):
        self._documents: Dict[str, Dict[str, ClientInfo]] = {}

    def _clients(self, document_id: str) -> Dict[str, ClientInfo]:
        return self._documents.setdefault(document_id, {})

    async def connect(self, document_id: str, client_id: str, websocket: WebSocket, role: str) -> ClientInfo:
        """Register a new client connection to a document. Accepts the
        WebSocket handshake and stores the client info."""
        await websocket.accept()
        client = ClientInfo(client_id, websocket, role)
        self._clients(document_id)[client_id] = client
        logger.info(f'Client connected: {client_id} to document {document_id} as {role} (total in doc: {len(self._clients(document_id))})')
        return client

    def disconnect(self, document_id: str, client_id: str) -> None:
        """Remove a client from a document's session. Called when a WebSocket
        connection closes."""
        clients = self._documents.get(document_id)
        if clients and client_id in clients:
            del clients[client_id]
            logger.info(f'Client disconnected: {client_id} from document {document_id} (remaining: {len(clients)})')
            if not clients:
                del self._documents[document_id]

    def get_client(self, document_id: str, client_id: str) -> Optional[ClientInfo]:
        clients = self._documents.get(document_id)
        return clients.get(client_id) if clients else None

    async def broadcast(self, document_id: str, message: dict, exclude_client: Optional[str]=None) -> None:
        """Send a JSON message to all clients connected to this document.

        Why exclude the sender?
            When User A sends an edit, we broadcast it to everyone else on the
            same document. User A already applied the edit locally
            (optimistic update); sending it back would cause a duplicate
            application. Instead, A gets an 'ack'.
        """
        clients = self._documents.get(document_id, {})
        disconnected = []
        for (client_id, client) in clients.items():
            if client_id == exclude_client:
                continue
            try:
                await client.websocket.send_json(message)
            except Exception:
                logger.warning(f'Failed to send to {client_id}, marking for disconnect')
                disconnected.append(client_id)
        for client_id in disconnected:
            self.disconnect(document_id, client_id)

    async def send_to(self, document_id: str, client_id: str, message: dict) -> None:
        """Send a JSON message to a specific client on a specific document."""
        client = self.get_client(document_id, client_id)
        if client is None:
            logger.warning(f'Tried to send to unknown client: {client_id} on document {document_id}')
            return
        try:
            await client.websocket.send_json(message)
        except Exception:
            logger.warning(f'Failed to send to {client_id}, disconnecting')
            self.disconnect(document_id, client_id)

    def update_cursor(self, document_id: str, client_id: str, position: int) -> None:
        """Update a client's cursor position."""
        client = self.get_client(document_id, client_id)
        if client:
            client.cursor_pos = position

    def get_client_list(self, document_id: str) -> list:
        """Get a list of all clients connected to a document and their info.

        Used to tell a newly connected client who else is in the session.
        """
        clients = self._documents.get(document_id, {})
        return [{'client_id': c.client_id, 'color': c.color, 'cursor_pos': c.cursor_pos, 'role': c.role} for c in clients.values()]

    def document_client_count(self, document_id: str) -> int:
        """Number of clients currently connected to a specific document."""
        return len(self._documents.get(document_id, {}))

    @property
    def total_client_count(self) -> int:
        """Number of clients currently connected across all documents."""
        return sum(len(clients) for clients in self._documents.values())

    def is_connected(self, document_id: str, client_id: str) -> bool:
        """Check if a client is currently connected to a document."""
        clients = self._documents.get(document_id)
        return bool(clients and client_id in clients)
