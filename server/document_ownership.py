"""
DocumentOwnership — single-writer-per-document coordination via a Redis lock.

Why this exists: once more than one app instance is running, a client can
connect to any of them (behind a load balancer), but Document.receive_operation()
— the transform-against-history step — must only ever run in one place for a
given document at a time, or two instances could transform two concurrent
edits against different histories and diverge. Rather than make the OT
transform itself distributed (hard, and not what any of the well-known
production editors actually do), exactly one instance becomes the "owner"
of each document: the only one that loads it and applies operations to it.
Other instances forward client operations to the owner instead (wired in a
later commit's pub/sub fanout) and relay the owner's broadcasts to their own
locally-connected clients.

Ownership is a Redis-backed lease (SET NX EX), renewed on a heartbeat while
held. If an owning instance dies, it simply stops renewing and the lease
expires — the next instance to try for that document becomes the new owner.
Renew/release use Lua scripts so an instance can only ever renew or release
a lock it still actually holds (compare-and-set on the stored instance id),
never someone else's.
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from typing import Optional

import redis.asyncio as redis

logger = logging.getLogger('loom.ownership')

LOCK_TTL_SECONDS = 10
RENEW_INTERVAL_SECONDS = 3

_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
else
    return 0
end
"""

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


class DocumentOwnership:
    def __init__(self, redis_client: redis.Redis, instance_id: Optional[str] = None):
        self._redis = redis_client
        self.instance_id = instance_id or str(uuid.uuid4())
        self._owned = set()
        self._renew_task: Optional[asyncio.Task] = None

    def _lock_key(self, document_id: str) -> str:
        return f'doc-lock:{document_id}'

    async def try_acquire(self, document_id: str) -> bool:
        """Attempt to become the owner of a document. Returns True if this
        instance is (now, or already was) the owner, False if another
        instance currently holds it."""
        if document_id in self._owned:
            return True
        key = self._lock_key(document_id)
        acquired = await self._redis.set(key, self.instance_id, nx=True, ex=LOCK_TTL_SECONDS)
        if acquired:
            self._owned.add(document_id)
            self._ensure_renew_loop()
            logger.info(f'Acquired ownership of {document_id} (instance {self.instance_id})')
        return bool(acquired)

    def is_owner(self, document_id: str) -> bool:
        """Local, non-Redis check — whether this instance currently believes
        it owns the document (kept in sync by the renew loop)."""
        return document_id in self._owned

    async def release(self, document_id: str) -> None:
        """Give up ownership. Only actually deletes the Redis key if this
        instance still holds it (compare-and-delete), so a stale release
        call can never clobber a different instance's lock."""
        if document_id not in self._owned:
            return
        key = self._lock_key(document_id)
        await self._redis.eval(_RELEASE_SCRIPT, 1, key, self.instance_id)
        self._owned.discard(document_id)
        logger.info(f'Released ownership of {document_id} (instance {self.instance_id})')

    async def release_all(self) -> None:
        """Called on shutdown so another instance can pick up every document
        this one was holding, without waiting out the full lease TTL."""
        if self._renew_task is not None:
            self._renew_task.cancel()
            self._renew_task = None
        for document_id in list(self._owned):
            await self.release(document_id)

    def _ensure_renew_loop(self) -> None:
        if self._renew_task is None or self._renew_task.done():
            self._renew_task = asyncio.create_task(self._renew_loop())

    async def _renew_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(RENEW_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                return
            for document_id in list(self._owned):
                key = self._lock_key(document_id)
                try:
                    renewed = await self._redis.eval(_RENEW_SCRIPT, 1, key, self.instance_id, LOCK_TTL_SECONDS)
                except Exception as e:
                    logger.error(f'Error renewing lock for {document_id}: {e}')
                    continue
                if not renewed:
                    # Lost the lease (e.g. a long GC/scheduling pause let it
                    # expire and another instance took over) — stop acting
                    # as owner. Whoever's calling try_acquire/is_owner will
                    # see this and fail over accordingly.
                    logger.warning(f'Lost ownership of {document_id} (lease expired before renewal)')
                    self._owned.discard(document_id)
