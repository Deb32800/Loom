"""
Tests for the Loom Document Manager
=====================================

These tests verify that the Document class correctly:
1. Applies operations and tracks revisions
2. Transforms late-arriving operations against the history
3. Handles edge cases (NoOp, future revision, etc.)

Run with:
    cd /Users/deb/Documents/Loom
    source venv/bin/activate
    pytest tests/test_document.py -v
"""

import pytest
from server.ot_engine import Insert, Delete, NoOp
from server.document import Document, OperationEntry


class TestDocumentBasics:
    """Test basic document operations — no concurrent edits yet."""

    def test_initial_state(self):
        doc = Document("Hello World")
        assert doc.content == "Hello World"
        assert doc.revision == 0
        assert doc.history == []

    def test_initial_empty_document(self):
        doc = Document()
        assert doc.content == ""
        assert doc.revision == 0

    def test_single_insert(self):
        doc = Document("Hello")
        result = doc.receive_operation(
            op=Insert(5, " World"),
            client_revision=0,
            client_id="user_a"
        )
        assert doc.content == "Hello World"
        assert doc.revision == 1
        assert result.revision == 1
        assert result.client_id == "user_a"

    def test_single_delete(self):
        doc = Document("Hello World")
        result = doc.receive_operation(
            op=Delete(5, 6),
            client_revision=0,
            client_id="user_a"
        )
        assert doc.content == "Hello"
        assert doc.revision == 1

    def test_sequential_operations_same_client(self):
        """One client sends multiple operations in sequence."""
        doc = Document("AB")

        doc.receive_operation(Insert(2, "CD"), client_revision=0, client_id="a")
        assert doc.content == "ABCD"
        assert doc.revision == 1

        doc.receive_operation(Insert(4, "EF"), client_revision=1, client_id="a")
        assert doc.content == "ABCDEF"
        assert doc.revision == 2

        doc.receive_operation(Delete(0, 1), client_revision=2, client_id="a")
        assert doc.content == "BCDEF"
        assert doc.revision == 3

    def test_history_is_recorded(self):
        doc = Document("Hello")
        doc.receive_operation(Insert(5, "!"), client_revision=0, client_id="a")
        doc.receive_operation(Insert(0, "Say "), client_revision=1, client_id="b")

        assert len(doc.history) == 2
        assert doc.history[0].revision == 1
        assert doc.history[0].client_id == "a"
        assert doc.history[1].revision == 2
        assert doc.history[1].client_id == "b"

    def test_get_state(self):
        doc = Document("Hello", document_id="doc_123")
        doc.receive_operation(Insert(5, "!"), client_revision=0, client_id="a")

        state = doc.get_state()
        assert state["content"] == "Hello!"
        assert state["revision"] == 1
        assert state["document_id"] == "doc_123"


class TestDocumentTransform:
    """Test that late-arriving operations are correctly transformed."""

    def test_client_one_revision_behind(self):
        """Client is 1 revision behind — transform against 1 missed op."""
        doc = Document("Hello")

        # User A inserts at the beginning (server applies this first)
        doc.receive_operation(
            Insert(0, "X"), client_revision=0, client_id="a"
        )
        # Server: "XHello", revision 1

        # User B also made their edit at revision 0 (concurrent!)
        # They want to insert at position 5 ("Hello" → "Hello!")
        # But since A inserted "X" at 0, B's position needs to shift to 6
        result = doc.receive_operation(
            Insert(5, "!"), client_revision=0, client_id="b"
        ) 
        assert doc.content == "XHello!"
        assert doc.revision == 2

    def test_client_two_revisions_behind(self):
        """Client is 2 revisions behind — transform against 2 missed ops."""
        doc = Document("ABCDE")

        # Revision 1: User A inserts "X" at start
        doc.receive_operation(
            Insert(0, "X"), client_revision=0, client_id="a"
        )
        
        doc.receive_operation(
            Insert(0, "Y"), client_revision=1, client_id="a"
        )
        
        result = doc.receive_operation(
            Insert(3, "_"), client_revision=0, client_id="b"
        )
        assert doc.content == "YXABC_DE"
        assert doc.revision == 3

    def test_concurrent_inserts_same_position(self):
        """Two clients insert at the same position concurrently."""
        doc = Document("Hello")

        # User A inserts "X" at position 3
        doc.receive_operation(
            Insert(3, "X"), client_revision=0, client_id="a"
        )
        
        doc.receive_operation(
            Insert(3, "Y"), client_revision=0, client_id="b"
        )
        # "HelYXlo" — B's insert goes at 3 (incoming op wins tie-break),
        # A's "X" gets pushed to 4
        assert doc.content == "HelYXlo"
        assert doc.revision == 2

    def test_concurrent_insert_and_delete(self):
        """One client inserts, another deletes concurrently."""
        doc = Document("ABCDE")

        # User A deletes "BC" (positions 1-2)
        doc.receive_operation(
            Delete(1, 2), client_revision=0, client_id="a"
        )
        
        doc.receive_operation(
            Insert(4, "X"), client_revision=0, client_id="b"
        )
        assert doc.content == "ADXE"
        assert doc.revision == 2

    def test_concurrent_deletes_no_overlap(self):
        """Two clients delete different regions concurrently."""
        doc = Document("ABCDEFGH")

        # User A deletes "AB" (positions 0-1)
        doc.receive_operation(
            Delete(0, 2), client_revision=0, client_id="a"
        )
        
        doc.receive_operation(
            Delete(6, 2), client_revision=0, client_id="b"
        )
        assert doc.content == "CDEF"
        assert doc.revision == 2

    def test_concurrent_deletes_with_overlap(self):
        """Two clients delete overlapping regions — the overlap is only deleted once."""
        doc = Document("ABCDEFGH")

        # User A deletes positions 2-5 ("CDEF")
        doc.receive_operation(
            Delete(2, 4), client_revision=0, client_id="a"
        )
        
        doc.receive_operation(
            Delete(4, 4), client_revision=0, client_id="b"
        )
        assert doc.content == "AB"
        assert doc.revision == 2


class TestDocumentEdgeCases:
    """Test error handling and edge cases."""

    def test_noop_is_ignored(self):
        doc = Document("Hello")
        result = doc.receive_operation(
            op=NoOp(), client_revision=0, client_id="a"
        )
        assert result is None
        assert doc.content == "Hello"
        assert doc.revision == 0

    def test_future_revision_raises(self):
        doc = Document("Hello")
        with pytest.raises(ValueError, match="ahead of server"):
            doc.receive_operation(
                Insert(0, "X"), client_revision=5, client_id="a"
            )

    def test_negative_revision_raises(self):
        doc = Document("Hello")
        with pytest.raises(ValueError, match="must be >= 0"):
            doc.receive_operation(
                Insert(0, "X"), client_revision=-1, client_id="a"
            )

    def test_operation_out_of_bounds_raises(self):
        doc = Document("Hi")
        with pytest.raises(ValueError):
            doc.receive_operation(
                Insert(100, "X"), client_revision=0, client_id="a"
            )

    def test_up_to_date_client_no_transform(self):
        """Client at current revision — no transform needed."""
        doc = Document("Hello")
        doc.receive_operation(
            Insert(5, "!"), client_revision=0, client_id="a"
        )
        # Client B is now at revision 1 (up to date)
        doc.receive_operation(
            Insert(0, "Say "), client_revision=1, client_id="b"
        )
        assert doc.content == "Say Hello!"
        assert doc.revision == 2


class TestDocumentRealisticScenario:
    """Simulate a realistic editing session."""

    def test_three_users_editing(self):
        """Three users editing the same document simultaneously."""
        doc = Document("The quick brown fox")

        # All three make edits based on revision 0
        # User A: prepend "A: "
        doc.receive_operation(
            Insert(0, "A: "), client_revision=0, client_id="a"
        )
        # Server: "A: The quick brown fox", rev 1

        # User B (at rev 0): append " jumps"
        doc.receive_operation(
            Insert(19, " jumps"), client_revision=0, client_id="b"
        )
        # Transform shifts position by 3 (len("A: "))
        # Server: "A: The quick brown fox jumps", rev 2

        # User C (at rev 0): delete "quick " (pos 4, count 6)
        doc.receive_operation(
            Delete(4, 6), client_revision=0, client_id="c"
        )
        # Transform vs A's Insert(0,"A: "): pos 4 → 7
        # Transform vs B's Insert(22," jumps"): no change (delete is before insert)
        # Server: "A: The brown fox jumps", rev 3

        assert doc.revision == 3
        assert "A: " in doc.content
        assert "jumps" in doc.content
        assert "quick" not in doc.content
