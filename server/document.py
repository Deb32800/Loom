"""
Loom Document Manager — The Server's Source of Truth
=====================================================

This module manages the server-side document state. It's the "memory"
that wraps the OT engine (the "brain").

Think of it this way:
    - OT Engine:        "Here's how to transform two operations" (pure math)
    - Document Manager:  "Here's the document, its version, and its history" (state)

The Document Manager does THREE things:
    1. Stores the current document text and revision number
    2. Keeps a history of all operations (needed for transforming late arrivals)
    3. Receives operations from clients, transforms them if needed, and applies them

WHY DO WE NEED REVISION NUMBERS?
    Imagine User A is typing fast and sends 3 operations.
    Meanwhile, User B sends an operation based on an older version.
    
    B's operation was made against revision 5, but the server is now at revision 8.
    We need to transform B's operation against revisions 6, 7, and 8
    before we can apply it. The revision number tells us HOW FAR BEHIND
    the client is, and the history tells us WHAT HAPPENED in between.

    Server timeline:
        rev 5 → op6 → rev 6 → op7 → rev 7 → op8 → rev 8
                                                      ↑ server is here
        
    Client B sends op based on rev 5:
        We transform it against op6, then op7, then op8.
        Now it's safe to apply against rev 8.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Union, Optional
from server.ot_engine import Insert, Delete, NoOp, Operation, apply, transform

@dataclass
class OperationEntry:
    
    op: Union[Operation, NoOp]
    revision: int
    client_id: str

class Document:
    """Server-side document state manager.
    
    This is the SINGLE SOURCE OF TRUTH for the document.
    All clients sync against this.
    
    Attributes:
        content:   The current document text.
        revision:  The current revision number (starts at 0).
        history:   List of all operations applied, in order.
                   history[0] is the op that created revision 1.
                   history[i] is the op that created revision i+1.
    
    Usage:
        doc = Document("Hello World")
        
        # Client sends an operation based on their last known revision
        result = doc.receive_operation(
            op=Insert(5, "!"),
            client_revision=0,
            client_id="user_abc"
        )
        
        # result contains the (possibly transformed) operation
        # and the new revision number — broadcast this to all clients
    """

    MAX_HISTORY = 1000

    def __init__(self, content: str='', document_id: str='default'):
        """Initialize a new document.
        
        Args:
            content:     Initial document text. Empty string by default.
            document_id: Unique identifier for this document.
        """
        self.content: str = content
        self.revision: int = 0
        self.base_revision: int = 0  # The revision number before the oldest operation in history
        self.history: list[OperationEntry] = []
        self.document_id: str = document_id

    def receive_operation(self, op: Union[Operation, NoOp], client_revision: int, client_id: str) -> Optional[OperationEntry]:
        """Receive an operation from a client and apply it.
        
        This is THE CORE METHOD. Here's what it does step by step:
        
        1. Check if the client's revision matches the server's revision.
           - If yes: no transform needed, apply directly.
           - If no:  the client is behind. Transform the operation against
                     every operation the client hasn't seen yet.
        
        2. Apply the (possibly transformed) operation to the document.
        
        3. Increment the revision number.
        
        4. Add the operation to the history.
        
        5. Return the result (to be broadcast to other clients).
        
        Args:
            op:              The operation the client wants to apply.
            client_revision: The revision number the client was at when
                            they created this operation. This tells us
                            how many operations they've "missed."
            client_id:       Which client sent this.
        
        Returns:
            OperationEntry with the (possibly transformed) operation
            and new revision number. Returns None if the operation
            was invalid (e.g., client_revision is in the future).
        
        Raises:
            ValueError: If client_revision is negative or in the future.
        
        Example:
            Server is at revision 5.
            Client sends Insert(3, "X") based on revision 3.
            
            The client missed operations at revisions 4 and 5.
            We transform their Insert against those two operations.
            Then apply the transformed Insert and create revision 6.
        """
        if client_revision < 0:
            raise ValueError(f'client_revision must be >= 0, got {client_revision}')
        if client_revision > self.revision:
            raise ValueError(f"client_revision {client_revision} is ahead of server revision {self.revision}. This shouldn't happen — the client thinks it's in the future!")
        if client_revision < self.base_revision:
            raise ValueError(f"client_revision {client_revision} is too old (server base is {self.base_revision}). Client must resync.")

        if isinstance(op, NoOp):
            return None
            
        if client_revision < self.revision:
            missed_ops = self.history[client_revision - self.base_revision:]
            for entry in missed_ops:
                if isinstance(entry.op, NoOp):
                    continue
                (op, _) = transform(op, entry.op)
                if isinstance(op, NoOp):
                    return None
                    
        try:
            self.content = apply(self.content, op)
        except ValueError as e:
            raise ValueError(f'Failed to apply operation after transform: {e}. Op: {op}, Document length: {len(self.content)}, Revision: {self.revision}')
            
        self.revision += 1
        entry = OperationEntry(op=op, revision=self.revision, client_id=client_id)
        self.history.append(entry)
        
        # Prune history if it gets too large
        if len(self.history) > self.MAX_HISTORY:
            # Keep the most recent half of the history
            keep = self.MAX_HISTORY // 2
            pruned_count = len(self.history) - keep
            self.history = self.history[-keep:]
            self.base_revision += pruned_count
            
        return entry

    def get_state(self) -> dict:
        """Get the current document state for syncing a new client.
        
        When a new client connects, we send them:
        - The full document content
        - The current revision number
        
        They can then start sending operations based on this revision.
        
        Returns:
            Dict with 'content', 'revision', and 'document_id'.
        """
        return {'content': self.content, 'revision': self.revision, 'document_id': self.document_id}

    def __repr__(self) -> str:
        preview = self.content[:50] + '...' if len(self.content) > 50 else self.content
        return f'Document(id={self.document_id!r}, rev={self.revision}, len={len(self.content)}, content={preview!r})'