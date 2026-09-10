"""
Loom Redis client — shared connection used for cross-instance coordination:
the WS-ticket store, document-ownership locks, and pub/sub fanout (the
latter two land in later commits; this module just owns the connection).
"""
from __future__ import annotations
import os
import redis.asyncio as redis

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')


def create_redis_client() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)
