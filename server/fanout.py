"""
Redis-backed cross-instance fanout for Loom's WebSocket layer.

Three pub/sub channels per document:
    doc-ops:{id}       - non-owner instances forward client operations here;
                          only the current owner subscribes.
    doc-broadcast:{id} - the owner publishes each processed operation's
                          result here; every instance with local clients on
                          that document subscribes and relays it (as an
                          'ack' to the sender if they're locally connected,
                          an 'operation' broadcast to everyone else locally
                          connected).
    doc-control:{id}   - presence join/leave and document_deleted, relayed
                          verbatim (minus the sender, where relevant) to
                          local clients on any instance.

Plus one plain Redis key per document (not pub/sub):
    doc-state:{id}     - {"content":..., "revision":...}, kept fresh by the
                          owner on every applied operation. A newly
                          connecting client on a non-owner instance uses
                          this as its sync baseline, since that instance
                          has no in-memory Document of its own.

Deliberately out of scope (see docs/ARCHITECTURE.md for the full writeup):
    - Cursor position and the full cross-instance client list are NOT
      fanned out — local-only per instance. They're frequent, cosmetic,
      non-authoritative; fanning them out would add steady Redis traffic
      for something that doesn't affect document correctness, unlike ops,
      ownership, and presence (all comparatively rare).
    - If the owning instance's local clients all disconnect while a
      *different* instance still has clients on the same document,
      ownership releases (tied to DocumentRegistry's own eviction grace
      period) and the document is briefly unowned until a new connection
      re-acquires it. A forward that lands in that gap is dropped rather
      than queued — real clients reconnect, and handling it losslessly
      needs either document-aware load-balancer routing or a
      request/response protocol on top of pub/sub, both bigger than this
      project's scope calls for.
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

import redis.asyncio as redis

logger = logging.getLogger('loom.fanout')


class RedisFanout:
    def __init__(self, redis_client: redis.Redis, session_manager, on_forwarded_operation: Callable[[str, dict], Awaitable[None]]):
        self._redis = redis_client
        self._session_manager = session_manager
        self._on_forwarded_operation = on_forwarded_operation
        self._pubsub = redis_client.pubsub()
        self._listen_task: Optional[asyncio.Task] = None
        self._subscriber_counts: dict = {}  # document_id -> local-client refcount for broadcast+control
        self._ops_subscribed: set = set()   # document_ids we're subscribed to doc-ops for (i.e. we're owner)

    async def start(self) -> None:
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def stop(self) -> None:
        if self._listen_task is not None:
            self._listen_task.cancel()
            self._listen_task = None
        await self._pubsub.aclose()

    # -- broadcast + control subscription, refcounted by local clients --

    async def ensure_document_subscription(self, document_id: str) -> None:
        count = self._subscriber_counts.get(document_id, 0)
        if count == 0:
            await self._pubsub.subscribe(f'doc-broadcast:{document_id}', f'doc-control:{document_id}')
        self._subscriber_counts[document_id] = count + 1

    async def release_document_subscription(self, document_id: str) -> None:
        count = self._subscriber_counts.get(document_id, 0) - 1
        if count <= 0:
            self._subscriber_counts.pop(document_id, None)
            await self._pubsub.unsubscribe(f'doc-broadcast:{document_id}', f'doc-control:{document_id}')
        else:
            self._subscriber_counts[document_id] = count

    # -- ops subscription: only while we're the owner --

    async def ensure_ops_subscription(self, document_id: str) -> None:
        if document_id not in self._ops_subscribed:
            self._ops_subscribed.add(document_id)
            await self._pubsub.subscribe(f'doc-ops:{document_id}')

    async def release_ops_subscription(self, document_id: str) -> None:
        if document_id in self._ops_subscribed:
            self._ops_subscribed.discard(document_id)
            await self._pubsub.unsubscribe(f'doc-ops:{document_id}')

    # -- publishing --

    async def forward_operation(self, document_id: str, op: dict, client_revision: int, client_id: str) -> None:
        await self._redis.publish(f'doc-ops:{document_id}', json.dumps({'op': op, 'revision': client_revision, 'client_id': client_id}))

    async def publish_broadcast(self, document_id: str, op: dict, revision: int, client_id: str, noop: bool = False) -> None:
        await self._redis.publish(f'doc-broadcast:{document_id}', json.dumps({'op': op, 'revision': revision, 'client_id': client_id, 'noop': noop}))

    async def publish_control(self, document_id: str, message: dict) -> None:
        await self._redis.publish(f'doc-control:{document_id}', json.dumps(message))

    # -- doc-state cache (plain key, not pub/sub) --

    async def get_cached_state(self, document_id: str):
        raw = await self._redis.get(f'doc-state:{document_id}')
        if raw is None:
            return None
        data = json.loads(raw)
        return data['content'], data['revision']

    async def set_cached_state(self, document_id: str, content: str, revision: int) -> None:
        await self._redis.set(f'doc-state:{document_id}', json.dumps({'content': content, 'revision': revision}))

    # -- dispatch loop --

    async def _listen_loop(self) -> None:
        try:
            async for message in self._pubsub.listen():
                if message['type'] != 'message':
                    continue
                channel = message['channel']
                try:
                    await self._dispatch(channel, json.loads(message['data']))
                except Exception as e:
                    logger.error(f'Error handling pubsub message on {channel}: {e}')
        except asyncio.CancelledError:
            return

    async def _dispatch(self, channel: str, data: dict) -> None:
        if channel.startswith('doc-broadcast:'):
            await self._relay_broadcast(channel[len('doc-broadcast:'):], data)
        elif channel.startswith('doc-control:'):
            document_id = channel[len('doc-control:'):]
            # join/leave carry client_id (excluded so a client doesn't get
            # its own join/leave echoed back); document_deleted doesn't, so
            # exclude_client is simply None there — no special-casing needed.
            await self._session_manager.broadcast(document_id, data, exclude_client=data.get('client_id'))
        elif channel.startswith('doc-ops:'):
            await self._on_forwarded_operation(channel[len('doc-ops:'):], data)

    async def _relay_broadcast(self, document_id: str, data: dict) -> None:
        client_id = data['client_id']
        revision = data['revision']
        op = data['op']
        is_noop = data.get('noop', False)
        if self._session_manager.is_connected(document_id, client_id):
            await self._session_manager.send_to(document_id, client_id, {'type': 'ack', 'revision': revision})
        if not is_noop:
            await self._session_manager.broadcast(document_id, {'type': 'operation', 'op': op, 'revision': revision, 'client_id': client_id}, exclude_client=client_id)
