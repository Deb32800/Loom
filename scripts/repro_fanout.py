#!/usr/bin/env python3
"""
Reproduces the exact concurrency pattern main.py + fanout.py use against a
LOCAL Redis, with no Postgres/Upstash/Render involved, to isolate whether
the "op processed but ack never arrives" bug is a code-level race in
RedisFanout or something specific to the Upstash deployment.

Mirrors real usage: RedisFanout.start() runs the listen loop as a
background task, then many concurrent "connections" call
ensure_document_subscription() (exactly what main.py's websocket_endpoint
does per client) while a publish happens - same shape as
_process_and_publish -> publish_broadcast -> _relay_broadcast.

Usage:
    python scripts/repro_fanout.py                    # local redis://localhost:6379/0
    python scripts/repro_fanout.py "rediss://default:<password>@<host>:6379"   # test a real Redis directly
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import redis.asyncio as redis
from server.fanout import RedisFanout


class FakeSessionManager:
    """Records what would have been sent to real WebSocket clients."""
    def __init__(self):
        self.acks = []
        self.broadcasts = []

    def is_connected(self, document_id, client_id):
        return True

    async def send_to(self, document_id, client_id, message):
        self.acks.append((client_id, message))

    async def broadcast(self, document_id, message, exclude_client=None):
        self.broadcasts.append((message, exclude_client))


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('url', nargs='?', default='redis://localhost:6379/0')
    args = parser.parse_args()

    redis_client = redis.from_url(args.url, decode_responses=True)
    await redis_client.ping()
    print(f'Connected to Redis at {args.url.split("@")[-1] if "@" in args.url else args.url}.')

    session_manager = FakeSessionManager()

    async def on_forwarded_operation(document_id, data):
        pass  # not exercised in this repro

    fanout = RedisFanout(redis_client, session_manager, on_forwarded_operation)
    await fanout.start()

    document_id = 'repro-doc'
    n_clients = 20

    print(f'Simulating {n_clients} concurrent connections joining the same document...')
    # This is exactly what main.py's websocket_endpoint does per connection:
    # ensure_document_subscription() called concurrently as clients connect.
    await asyncio.gather(*[fanout.ensure_document_subscription(document_id) for _ in range(n_clients)])
    print('All subscriptions registered.')

    await asyncio.sleep(0.5)  # let the listen loop settle

    print('Publishing 10 broadcast messages, exactly like _process_and_publish does...')
    for i in range(10):
        await fanout.publish_broadcast(document_id, {'type': 'insert', 'position': 0, 'text': 'x'}, revision=i + 1, client_id=f'client-{i}')

    # Give the listen loop time to dispatch everything.
    await asyncio.sleep(2)

    print(f'\nResults: {len(session_manager.acks)}/10 acks delivered, {len(session_manager.broadcasts)} broadcasts relayed.')
    if len(session_manager.acks) < 10:
        print('BUG REPRODUCED LOCALLY: not all published messages made it back through the listen loop.')
    else:
        print('All acks delivered locally — the code path is fine against a local Redis.')

    await fanout.stop()
    await redis_client.aclose()


if __name__ == '__main__':
    asyncio.run(main())
