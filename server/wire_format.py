"""JSON <-> OT operation conversion, shared by the WebSocket endpoint
(local clients) and the Redis fanout module (forwarded ops from other
instances) so both speak the exact same wire format."""
from __future__ import annotations
from server.ot_engine import Insert, Delete, NoOp


def parse_operation(op_data: dict):
    """Convert a JSON operation dict into an Insert or Delete object.

    Expected format:
        {"type": "insert", "position": 5, "text": "hello"}
        {"type": "delete", "position": 5, "count": 3}

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
    """Convert an Insert/Delete/NoOp into a JSON-serializable dict. The
    reverse of parse_operation."""
    if isinstance(op, Insert):
        return {'type': 'insert', 'position': op.position, 'text': op.text}
    elif isinstance(op, Delete):
        return {'type': 'delete', 'position': op.position, 'count': op.count}
    elif isinstance(op, NoOp):
        return {'type': 'noop'}
    else:
        raise ValueError(f'Cannot serialize operation: {type(op)}')
