"""
DocumentRegistry — in-memory registry of active Document instances.

Earlier phases had exactly one global Document. Now there are many, owned per
user, so documents are loaded from Postgres lazily (on first connection) and
evicted from memory (after a final flush) once the last connected client
disconnects and a grace period elapses — so a quick reconnect doesn't pay the
reload cost, but an abandoned document doesn't sit in memory forever.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Awaitable, Callable, Dict, Optional

from server.database import Database
from server.document import Document, OperationEntry

logger = logging.getLogger('loom.registry')

EVICTION_GRACE_SECONDS = 60


class _ActiveDocument:
    def __init__(self, document: Document):
        self.document = document
        self.ref_count = 0
        self.eviction_task: Optional[asyncio.Task] = None


class DocumentRegistry:
    def __init__(self, db: Database, on_evict: Optional[Callable[[str], Awaitable[None]]] = None):
        self._db = db
        self._active: Dict[str, _ActiveDocument] = {}
        self._load_locks: Dict[str, asyncio.Lock] = {}
        # Called (if set) whenever a document leaves memory — either via the
        # grace-period eviction below or an explicit evict() — so a caller
        # coordinating cross-instance state (e.g. releasing a Redis
        # ownership lock) finds out without DocumentRegistry needing to know
        # anything about Redis itself.
        self._on_evict = on_evict

    def _load_lock(self, document_id: str) -> asyncio.Lock:
        lock = self._load_locks.get(document_id)
        if lock is None:
            lock = asyncio.Lock()
            self._load_locks[document_id] = lock
        return lock

    async def acquire(self, document_id: str) -> Document:
        """Get the in-memory Document for document_id, loading it from the
        database on first access. Increments the connected-client refcount
        and cancels any pending eviction.

        Raises KeyError if no such document exists in the database.
        """
        active = self._active.get(document_id)
        if active is None:
            async with self._load_lock(document_id):
                # Re-check: another connection may have loaded it while we waited.
                active = self._active.get(document_id)
                if active is None:
                    active = await self._load(document_id)
                    self._active[document_id] = active
        if active.eviction_task is not None:
            active.eviction_task.cancel()
            active.eviction_task = None
        active.ref_count += 1
        return active.document

    def release(self, document_id: str) -> None:
        """Called when a client disconnects. Once the last client for a
        document disconnects, schedule it for eviction after a grace period."""
        active = self._active.get(document_id)
        if active is None:
            return
        active.ref_count = max(0, active.ref_count - 1)
        if active.ref_count == 0:
            active.eviction_task = asyncio.create_task(self._evict_after_grace(document_id))

    def get_active(self, document_id: str) -> Optional[Document]:
        """Peek at a document without loading it or affecting its refcount."""
        active = self._active.get(document_id)
        return active.document if active else None

    async def evict(self, document_id: str) -> None:
        """Immediately drop a document from memory without flushing it —
        for when the caller has already deleted its rows from the database
        (e.g. DELETE /api/documents/{id}) and a flush would just recreate a
        broken, ownerless row. Any pending scheduled eviction is cancelled."""
        active = self._active.pop(document_id, None)
        if active is not None and active.eviction_task is not None:
            active.eviction_task.cancel()
        self._load_locks.pop(document_id, None)
        if self._on_evict is not None:
            await self._on_evict(document_id)

    async def _load(self, document_id: str) -> _ActiveDocument:
        saved = await self._db.load_document(document_id)
        if saved is None:
            raise KeyError(document_id)
        content, revision = saved
        document = Document(content=content, document_id=document_id)
        document.revision = revision
        ops = await self._db.load_operations(document_id)
        for op_data in ops:
            document.history.append(OperationEntry(op=op_data['op'], revision=op_data['revision'], client_id=op_data['client_id']))
        logger.info(f'Loaded document {document_id}: {len(content)} chars, revision {revision}, {len(ops)} ops in history')
        return _ActiveDocument(document)

    async def _evict_after_grace(self, document_id: str) -> None:
        try:
            await asyncio.sleep(EVICTION_GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        active = self._active.get(document_id)
        if active is None or active.ref_count > 0:
            return
        await self._db.save_document(document_id, active.document.content, active.document.revision)
        del self._active[document_id]
        self._load_locks.pop(document_id, None)
        logger.info(f'Evicted document {document_id} from memory (grace period elapsed)')
        if self._on_evict is not None:
            await self._on_evict(document_id)

    async def flush_all(self) -> None:
        """Flush every active document's current state to the database. Used
        on server shutdown."""
        for document_id, active in self._active.items():
            await self._db.save_document(document_id, active.document.content, active.document.revision)
