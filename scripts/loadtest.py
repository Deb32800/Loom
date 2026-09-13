#!/usr/bin/env python3
"""
Loom WebSocket load/concurrency test.

Signs up one throwaway test user, creates one shared document, then opens
an increasing number of concurrent WebSocket connections to it (simulating
that many browser tabs editing together) and measures connect success rate
plus operation -> ack round-trip latency at each concurrency level.

Because Loom deliberately routes every document through a single-writer
owner instance (see docs/ARCHITECTURE.md), hammering one shared document is
the realistic worst case: it's exactly what "N people editing the same doc
at once" looks like, and it's where ack latency will visibly climb as
concurrency rises, well before connection count alone becomes the limit.

Usage:
    python scripts/loadtest.py https://loom-zuw6.onrender.com
    python scripts/loadtest.py https://loom-zuw6.onrender.com --levels 5,15,30,60 --ops 8
"""
from __future__ import annotations
import argparse
import asyncio
import json
import ssl
import statistics
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import websockets

SSL_CTX = ssl.create_default_context()


def _http_json(method: str, url: str, body: Optional[dict] = None, token: Optional[str] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def _ws_url(base_url: str, ticket: str) -> str:
    parts = urlsplit(base_url)
    scheme = 'wss' if parts.scheme == 'https' else 'ws'
    return urlunsplit((scheme, parts.netloc, '/ws', f'ticket={ticket}', ''))


@dataclass
class ClientResult:
    connected: bool = False
    error: Optional[str] = None
    ack_latencies_ms: list = field(default_factory=list)


async def _run_client(base_url: str, token: str, document_id: str, client_index: int, ops: int) -> ClientResult:
    result = ClientResult()
    try:
        ticket = _http_json('POST', f'{base_url}/api/documents/{document_id}/ws-ticket', token=token)['ticket']
    except Exception as e:
        result.error = f'ticket fetch failed: {e}'
        return result

    try:
        async with websockets.connect(_ws_url(base_url, ticket), open_timeout=15) as ws:
            sync_raw = await asyncio.wait_for(ws.recv(), timeout=15)
            sync = json.loads(sync_raw)
            result.connected = True
            revision = sync['revision']

            for i in range(ops):
                op = {'type': 'insert', 'position': 0, 'text': 'x'}  # always valid regardless of doc length
                send_t = time.monotonic()
                await ws.send(json.dumps({'type': 'operation', 'revision': revision, 'op': op}))
                # Drain messages until our ack (or an operation from someone else) arrives.
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    if msg.get('type') == 'ack':
                        result.ack_latencies_ms.append((time.monotonic() - send_t) * 1000)
                        revision = msg['revision']
                        break
                    elif msg.get('type') == 'operation':
                        revision = msg['revision']
                    elif msg.get('type') == 'error':
                        result.error = f"server error: {msg.get('message')}"
                        break
                await asyncio.sleep(0.05 + 0.1 * (client_index % 3) / 10)  # stagger, mimic typing
    except Exception as e:
        result.error = result.error or str(e)
    return result


async def _run_level(base_url: str, token: str, document_id: str, concurrency: int, ops: int) -> list:
    tasks = [_run_client(base_url, token, document_id, i, ops) for i in range(concurrency)]
    return await asyncio.gather(*tasks)


def _summarize(level: int, results: list) -> None:
    connected = [r for r in results if r.connected]
    failed = [r for r in results if not r.connected]
    all_latencies = [ms for r in results for ms in r.ack_latencies_ms]
    errors = [r.error for r in results if r.error]

    print(f'\n--- concurrency={level} ---')
    print(f'connected: {len(connected)}/{level}   failed: {len(failed)}')
    if all_latencies:
        all_latencies.sort()
        p50 = statistics.median(all_latencies)
        p95 = all_latencies[int(len(all_latencies) * 0.95) - 1]
        print(f'ack latency ms — avg: {statistics.mean(all_latencies):.0f}  p50: {p50:.0f}  p95: {p95:.0f}  max: {max(all_latencies):.0f}  (n={len(all_latencies)})')
    else:
        print('ack latency: no successful ops')
    if errors:
        sample = errors[:3]
        print(f'errors ({len(errors)} total), sample: {sample}')


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('url', help='Base URL of the deployed app, e.g. https://loom-zuw6.onrender.com')
    parser.add_argument('--levels', default='5,15,30', help='Comma-separated concurrency levels to test in sequence')
    parser.add_argument('--ops', type=int, default=6, help='Operations sent per simulated client')
    parser.add_argument('--pause', type=float, default=5.0, help='Seconds to pause between concurrency levels')
    args = parser.parse_args()

    base_url = args.url.rstrip('/')
    levels = [int(x) for x in args.levels.split(',')]

    username = f'loadtest_{uuid.uuid4().hex[:10]}'
    print(f'Signing up throwaway user: {username}')
    signup = _http_json('POST', f'{base_url}/api/auth/signup', {'username': username, 'password': 'loadtest-password-1'})
    token = signup['access_token']

    print('Creating shared test document')
    doc = _http_json('POST', f'{base_url}/api/documents', {'title': 'Load Test Doc'}, token=token)
    document_id = doc['id']
    print(f'Document: {document_id}')

    for level in levels:
        print(f'\nRunning {level} concurrent clients x {args.ops} ops each...')
        results = await _run_level(base_url, token, document_id, level, args.ops)
        _summarize(level, results)
        if level != levels[-1]:
            await asyncio.sleep(args.pause)

    print(f'\nDone. Test user "{username}" and its document ("{document_id}") are left in the DB — delete manually if you want to clean up.')


if __name__ == '__main__':
    asyncio.run(main())
