"""
Loom OT Engine — The Brain of Real-Time Collaboration
======================================================

This module implements Operational Transformation (OT) for plain text.
It's the same algorithm family that powers Google Docs.

There are exactly THREE things this module does:
    1. Define operations (Insert, Delete)
    2. Apply an operation to a document string
    3. Transform two concurrent operations so they converge

No networking, no database, no UI — just pure algorithm.
That's why we build and test this FIRST.

Key Property (Convergence / TP1):
    For any document d and concurrent operations a, b:
    
        apply(apply(d, a), b') == apply(apply(d, b), a')
    
    where (a', b') = transform(a, b)

    This means: no matter which order you apply the operations,
    you end up with the SAME document. That's the whole point of OT.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Union

@dataclass(frozen=True)
class Insert:
    """Insert text at a specific position in the document.
    
    Example:
        Insert(position=5, text="hello")
        On document "abcde" → "abcdehello"
    
    Attributes:
        position: 0-indexed character position where text will be inserted.
                  Text is inserted BEFORE the character at this position.
                  position=0 means insert at the very beginning.
                  position=len(doc) means insert at the very end.
        text:     The string to insert. Can be one character or many.
    """
    position: int
    text: str

    def __post_init__(self):
        """Validate the operation on creation."""
        if self.position < 0:
            raise ValueError(f'Insert position must be >= 0, got {self.position}')
        if not self.text:
            raise ValueError('Insert text must not be empty')

@dataclass(frozen=True)
class Delete:
    """Delete a number of characters starting at a position.
    
    Example:
        Delete(position=2, count=3)
        On document "abcde" → "ae"  (deleted "bcd")
    
    Attributes:
        position: 0-indexed start position of deletion.
        count:    Number of characters to delete.
                  Must be >= 1.
    """
    position: int
    count: int

    def __post_init__(self):
        """Validate the operation on creation."""
        if self.position < 0:
            raise ValueError(f'Delete position must be >= 0, got {self.position}')
        if self.count <= 0:
            raise ValueError(f'Delete count must be > 0, got {self.count}')
Operation = Union[Insert, Delete]

def apply(document: str, op: Operation) -> str:
    """Apply a single operation to a document string.
    
    Args:
        document: The current document text.
        op: The operation to apply (Insert or Delete).
    
    Returns:
        The new document text after applying the operation.
    
    Raises:
        ValueError: If the operation is out of bounds for this document.
        TypeError:  If op is not an Insert or Delete.
    
    Examples:
        >>> apply("Hello", Insert(5, " World"))
        'Hello World'
        >>> apply("Hello World", Delete(5, 6))
        'Hello'
    """
    if isinstance(op, Insert):
        if op.position > len(document):
            raise ValueError(f'Insert position {op.position} is out of bounds for document of length {len(document)}')
        return document[:op.position] + op.text + document[op.position:]
    elif isinstance(op, Delete):
        if op.position + op.count > len(document):
            raise ValueError(f'Delete range [{op.position}:{op.position + op.count}] exceeds document length {len(document)}')
        return document[:op.position] + document[op.position + op.count:]
    else:
        raise TypeError(f'Unknown operation type: {type(op)}')

def transform(op_a: Operation, op_b: Operation) -> 'tuple[Operation, Operation]':
    """Transform two concurrent operations for convergence.
    
    Given operations a and b, both created against the same document state,
    returns (a', b') such that:
    
        apply(apply(doc, a), b') == apply(apply(doc, b), a')
    
    This is the TP1 (Transformation Property 1) — the diamond property.
    
    Convention: when two inserts happen at the same position,
    operation 'a' wins (goes first). This is arbitrary but must be consistent.
    In practice, 'a' is the operation that arrived at the server first.
    
    Args:
        op_a: First concurrent operation.
        op_b: Second concurrent operation.
    
    Returns:
        Tuple of (a', b') — the transformed operations.
    """
    if isinstance(op_a, Insert) and isinstance(op_b, Insert):
        return _transform_insert_insert(op_a, op_b)
    elif isinstance(op_a, Insert) and isinstance(op_b, Delete):
        return _transform_insert_delete(op_a, op_b)
    elif isinstance(op_a, Delete) and isinstance(op_b, Insert):
        (b_prime, a_prime) = _transform_insert_delete(op_b, op_a)
        return (a_prime, b_prime)
    elif isinstance(op_a, Delete) and isinstance(op_b, Delete):
        return _transform_delete_delete(op_a, op_b)
    else:
        raise TypeError(f'Cannot transform {type(op_a).__name__} vs {type(op_b).__name__}')

def _transform_insert_insert(a: Insert, b: Insert) -> tuple[Insert, Insert]:
    """Transform two concurrent Insert operations.
    
    Three sub-cases based on positions:
    
    Case 1a: a.pos < b.pos
        a inserts before b, so b needs to shift right by len(a.text).
        a is unaffected because b is after it.
        
        Example: doc="Hello"
          a = Insert(0, "X")   → "XHello"
          b = Insert(3, "Y")   → "HelYlo"
          After transform: b' = Insert(4, "Y")  (shifted by 1)
    
    Case 1b: a.pos > b.pos
        b inserts before a, so a needs to shift right by len(b.text).
        
    Case 1c: a.pos == b.pos (TIE)
        Convention: a wins (goes first).
        So b shifts right by len(a.text).
    """
    if a.position < b.position:
        return (a, Insert(b.position + len(a.text), b.text))
    elif a.position > b.position:
        return (Insert(a.position + len(b.text), a.text), b)
    else:
        return (a, Insert(b.position + len(a.text), b.text))

def _transform_insert_delete(ins: Insert, dlt: Delete) -> tuple[Insert, Delete]:
    """Transform an Insert against a concurrent Delete.
    
    Sub-case 2a: Insert is BEFORE the delete region
        ins.pos <= dlt.pos
        The insert shifts the delete region right.
        
        Example: doc="ABCDE"
          ins = Insert(1, "X")   → "AXBCDE"
          dlt = Delete(3, 2)     → "ABCE" (deleted "DE" wait no, "DE" at pos 3-4)
          After transform:
            ins' = Insert(1, "X")     — unchanged
            dlt' = Delete(4, 2)       — shifted right by len("X")=1
            in easy words - the inserted text is inserted first and 
            the delete operation is shifted right by the length of the inserted text
    
    Sub-case 2b: Insert is AFTER the delete region
        ins.pos >= dlt.pos + dlt.count
        The delete shifts the insert position left.
        
        Example: doc="ABCDE"
          ins = Insert(4, "X")   → "ABCDXE"
          dlt = Delete(1, 2)     → "ADE"  (deleted "BC")
          After transform:
            ins' = Insert(2, "X")     — shifted left by count=2
            dlt' = Delete(1, 2)       — unchanged
    
    Sub-case 2c: Insert is INSIDE the delete region (THE TRICKY ONE)
        dlt.pos < ins.pos < dlt.pos + dlt.count
        
        The insert is in the middle of text being deleted.
        We PRESERVE the inserted text (user's intention matters!)
        but the insert lands at the start of the delete region,
        and the delete expands to cover the additional characters.
        
        Example: doc="ABCDE"
          ins = Insert(3, "XY")  → "ABCXYDE"
          dlt = Delete(2, 3)     → "AB"  (deleted "CDE")
          After transform:
            ins' = Insert(2, "XY")    — moved to delete's start
            dlt' = Delete(2, 5)       — expanded: 3 + len("XY") = 5
    """
    if ins.position <= dlt.position:
        return (ins, Delete(dlt.position + len(ins.text), dlt.count))
    elif ins.position >= dlt.position + dlt.count:
        return (Insert(ins.position - dlt.count, ins.text), dlt)
    else:
        return (_make_noop(), Delete(dlt.position, dlt.count + len(ins.text)))

def _transform_delete_delete(a: Delete, b: Delete) -> 'tuple[Operation, Operation]':
    """Transform two concurrent Delete operations.
    
    This is the most complex case because of potential overlaps.
    
    We compute the overlap region, then subtract it from each delete.
    Characters in the overlap are being deleted by BOTH operations,
    so each transformed operation only needs to delete the NON-overlapping part.
    """
    a_end = a.position + a.count
    b_end = b.position + b.count
    if a_end <= b.position:
        return (a, Delete(b.position - a.count, b.count))
    if b_end <= a.position:
        return (Delete(a.position - b.count, a.count), b)
    overlap_start = max(a.position, b.position)
    overlap_end = min(a_end, b_end)
    overlap_count = overlap_end - overlap_start
    a_prime_count = a.count - overlap_count
    b_prime_count = b.count - overlap_count
    if a.position <= b.position:
        a_prime_pos = a.position
        b_prime_pos = a.position
    else:
        a_prime_pos = b.position
        b_prime_pos = b.position
    if a_prime_count == 0 and b_prime_count == 0:
        return (_make_noop(), _make_noop())
    elif a_prime_count == 0:
        return (_make_noop(), Delete(b_prime_pos, b_prime_count))
    elif b_prime_count == 0:
        return (Delete(a_prime_pos, a_prime_count), _make_noop())
    else:
        return (Delete(a_prime_pos, a_prime_count), Delete(b_prime_pos, b_prime_count))

@dataclass(frozen=True)
class NoOp:
    """A no-operation. Applying this changes nothing.
    
    Created when two concurrent deletes fully overlap — one of them
    has nothing left to delete after the other is applied.
    """
    pass

def _make_noop() -> NoOp:
    """Create a no-op operation."""
    return NoOp()
_original_apply = apply

def apply(document: str, op: 'Union[Operation, NoOp]') -> str:
    """Apply an operation to a document. NoOp returns the document unchanged."""
    if isinstance(op, NoOp):
        return document
    return _original_apply(document, op)

def apply_all(document: str, ops: 'list[Union[Operation, NoOp]]') -> str:
    """Apply a sequence of operations to a document.
    
    Operations are applied left to right (in order).
    
    Example:
        >>> apply_all("Hello", [Insert(5, " World"), Insert(0, "Say ")])
        'Say Hello World'
    """
    for op in ops:
        document = apply(document, op)
    return document