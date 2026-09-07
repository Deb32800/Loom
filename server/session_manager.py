"""
Loom Session Manager — Tracking Connected Clients
===================================================

This module manages WebSocket connections. It knows:
    - Which clients are currently connected
    - Each client's WebSocket object (so we can send them messages)
    - Each client's cursor position (for showing remote cursors)

It does NOT handle OT or document state — that's the Document Manager's job.
This module is purely about connection management and message broadcasting.

WHY SEPARATE THIS FROM THE MAIN SERVER?
    Separation of concerns. The WebSocket endpoint in main.py handles
    message parsing and routing. This module handles "who is connected"
    and "how do I send a message to everyone." Keeping them separate
    makes both easier to understand and test.
"""

from __future__ import annotations
import json
import logging
from typing import Dict, Optional
from fastapi import WebSocket

# Set up logging so we can see connection events in the terminal
logger = logging.getLogger("loom.session")


class ClientInfo:
    """Information about a single connected client.
    
    Attributes:
        client_id:  Unique identifier for this client (e.g. "user_abc").
        websocket:  The WebSocket connection object.
        cursor_pos: The client's last known cursor position in the document.
        color:      A color assigned to this client for their remote cursor.
    """

    # Colors assigned to clients for their cursors.
    # We cycle through these as clients connect.
    CURSOR_COLORS = [
        "#E74C3C",  # red
        "#3498DB",  # blue
        "#2ECC71",  # green
        "#F39C12",  # orange
        "#9B59B6",  # purple
        "#1ABC9C",  # teal
        "#E67E22",  # dark orange
        "#E91E63",  # pink
    ]

    _color_index = 0  # class-level counter for cycling colors

    def __init__(self, client_id: str, websocket: WebSocket):
        self.client_id = client_id
        self.websocket = websocket
        self.cursor_pos: int = 0
        self.color = ClientInfo.CURSOR_COLORS[
            ClientInfo._color_index % len(ClientInfo.CURSOR_COLORS)
        ]
        ClientInfo._color_index += 1


class SessionManager:
    """Manages all connected WebSocket clients.
    
    This is the "phonebook" of the server — it knows who's connected
    and how to reach them.
    
    Usage:
        manager = SessionManager()
        
        # When a client connects
        await manager.connect(client_id, websocket)
        
        # Send a message to everyone except the sender
        await manager.broadcast(message_dict, exclude_client="user_abc")
        
        # When a client disconnects
        manager.disconnect(client_id)
    """

    def __init__(self):
        # Maps client_id -> ClientInfo
        # We use a dict for O(1) lookup by client_id
        self._clients: Dict[str, ClientInfo] = {}

    async def connect(self, client_id: str, websocket: WebSocket) -> ClientInfo:
        """Register a new client connection.
        
        This accepts the WebSocket handshake and stores the client info.
        
        Args:
            client_id: Unique identifier for this client.
            websocket: The WebSocket connection from FastAPI.
        
        Returns:
            ClientInfo for the newly connected client.
        """
        # Accept the WebSocket handshake.
        # Until we call this, the connection isn't established.
        # This is the server saying "yes, I'll talk to you."
        await websocket.accept()

        client = ClientInfo(client_id, websocket)
        self._clients[client_id] = client

        logger.info(
            f"Client connected: {client_id} "
            f"(color: {client.color}, total: {len(self._clients)})"
        )
        return client

    def disconnect(self, client_id: str) -> None:
        """Remove a client from the session.
        
        Called when a WebSocket connection closes (user closes tab,
        network drops, etc.)
        
        Args:
            client_id: The client to remove.
        """
        if client_id in self._clients:
            del self._clients[client_id]
            logger.info(
                f"Client disconnected: {client_id} "
                f"(remaining: {len(self._clients)})"
            )

    async def broadcast(
        self,
        message: dict,
        exclude_client: Optional[str] = None
    ) -> None:
        """Send a JSON message to all connected clients.
        
        Args:
            message:        The message dict to send (will be JSON-serialized).
            exclude_client: If set, don't send to this client.
                           Used when broadcasting an operation — we don't
                           send a client's own operation back to them.
                           Instead, they get an 'ack' message.
        
        Why exclude the sender?
            When User A sends an edit, we broadcast it to Users B and C.
            But User A already applied the edit locally (optimistic update).
            Sending it back to A would cause a duplicate application.
            Instead, A gets an 'ack' confirming the server accepted it.
        """
        # Collect clients that fail to receive (broken connections)
        disconnected = []

        for client_id, client in self._clients.items():
            if client_id == exclude_client:
                continue

            try:
                await client.websocket.send_json(message)
            except Exception:
                # Connection is broken — mark for cleanup
                logger.warning(f"Failed to send to {client_id}, marking for disconnect")
                disconnected.append(client_id)

        # Clean up any broken connections
        for client_id in disconnected:
            self.disconnect(client_id)

    async def send_to(self, client_id: str, message: dict) -> None:
        """Send a JSON message to a specific client.
        
        Used for:
            - Sending the initial document state when a client connects
            - Sending 'ack' messages back to the operation sender
        
        Args:
            client_id: The client to send to.
            message:   The message dict to send.
        """
        client = self._clients.get(client_id)
        if client is None:
            logger.warning(f"Tried to send to unknown client: {client_id}")
            return

        try:
            await client.websocket.send_json(message)
        except Exception:
            logger.warning(f"Failed to send to {client_id}, disconnecting")
            self.disconnect(client_id)

    def update_cursor(self, client_id: str, position: int) -> None:
        """Update a client's cursor position.
        
        Args:
            client_id: The client whose cursor moved.
            position:  New cursor position (character index in document).
        """
        client = self._clients.get(client_id)
        if client:
            client.cursor_pos = position

    def get_client_list(self) -> list:
        """Get a list of all connected clients and their info.
        
        Used to tell a newly connected client who else is in the session.
        
        Returns:
            List of dicts with client_id, color, and cursor_pos.
        """
        return [
            {
                "client_id": c.client_id,
                "color": c.color,
                "cursor_pos": c.cursor_pos,
            }
            for c in self._clients.values()
        ]

    @property
    def client_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._clients)

    def is_connected(self, client_id: str) -> bool:
        """Check if a client is currently connected."""
        return client_id in self._clients
