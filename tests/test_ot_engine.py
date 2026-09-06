"""
Tests for the Loom OT Engine
=============================

These tests verify the CORRECTNESS of our OT implementation.
The most important test is the CONVERGENCE test:

    apply(apply(doc, a), b') == apply(apply(doc, b), a')

If this holds for all operation combinations, our OT is correct.

Run with:
    cd /Users/deb/Documents/Loom
    source venv/bin/activate
    pytest tests/test_ot_engine.py -v
"""

import pytest
from server.ot_engine import Insert, Delete, NoOp, apply, transform, apply_all


# ═══════════════════════════════════════════════════════════════
# TEST GROUP 1: Operation Validation
# ═══════════════════════════════════════════════════════════════
# Make sure our operations reject invalid inputs.


class TestOperationValidation:
    """Test that operations validate their inputs on creation."""

    def test_insert_negative_position_raises(self):
        with pytest.raises(ValueError):
            Insert(position=-1, text="hello")

    def test_insert_empty_text_raises(self):
        with pytest.raises(ValueError):
            Insert(position=0, text="")

    def test_delete_negative_position_raises(self):
        with pytest.raises(ValueError):
            Delete(position=-1, count=1)

    def test_delete_zero_count_raises(self):
        with pytest.raises(ValueError):
            Delete(position=0, count=0)

    def test_delete_negative_count_raises(self):
        with pytest.raises(ValueError):
            Delete(position=0, count=-1)

    def test_valid_insert_creates_ok(self):
        op = Insert(position=0, text="hello")
        assert op.position == 0
        assert op.text == "hello"

    def test_valid_delete_creates_ok(self):
        op = Delete(position=5, count=3)
        assert op.position == 5
        assert op.count == 3


# ═══════════════════════════════════════════════════════════════
# TEST GROUP 2: Apply Function
# ═══════════════════════════════════════════════════════════════
# Test that operations are correctly applied to documents.


class TestApply:
    """Test the apply function — applying operations to documents."""

    # ── Insert tests ──────────────────────────────────────────

    def test_insert_at_beginning(self):
        result = apply("Hello", Insert(0, "Say "))
        assert result == "Say Hello"

    def test_insert_at_end(self):
        result = apply("Hello", Insert(5, " World"))
        assert result == "Hello World"

    def test_insert_in_middle(self):
        result = apply("Hllo", Insert(1, "e"))
        assert result == "Hello"

    def test_insert_into_empty_document(self):
        result = apply("", Insert(0, "Hello"))
        assert result == "Hello"

    def test_insert_single_character(self):
        result = apply("ab", Insert(1, "X"))
        assert result == "aXb"

    def test_insert_multi_character(self):
        result = apply("AE", Insert(1, "BCD"))
        assert result == "ABCDE"

    def test_insert_out_of_bounds_raises(self):
        with pytest.raises(ValueError):
            apply("Hello", Insert(10, "X"))

    # ── Delete tests ──────────────────────────────────────────

    def test_delete_from_beginning(self):
        result = apply("Hello World", Delete(0, 6))
        assert result == "World"

    def test_delete_from_end(self):
        result = apply("Hello World", Delete(5, 6))
        assert result == "Hello"

    def test_delete_from_middle(self):
        result = apply("Hello World", Delete(5, 1))
        assert result == "HelloWorld"

    def test_delete_single_character(self):
        result = apply("ABC", Delete(1, 1))
        assert result == "AC"

    def test_delete_entire_document(self):
        result = apply("Hello", Delete(0, 5))
        assert result == ""

    def test_delete_out_of_bounds_raises(self):
        with pytest.raises(ValueError):
            apply("Hi", Delete(0, 5))

    # ── NoOp tests ────────────────────────────────────────────

    def test_noop_changes_nothing(self):
        result = apply("Hello World", NoOp())
        assert result == "Hello World"

    # ── apply_all tests ───────────────────────────────────────

    def test_apply_all_multiple_inserts(self):
        result = apply_all("", [
            Insert(0, "Hello"),
            Insert(5, " World"),
        ])
        assert result == "Hello World"

    def test_apply_all_mixed_operations(self):
        result = apply_all("ABCDE", [
            Delete(4, 1),       # "ABCD"
            Insert(4, "EFG"),   # "ABCDEFG"
        ])
        assert result == "ABCDEFG"


# ═══════════════════════════════════════════════════════════════
# TEST GROUP 3: Transform — Insert vs Insert
# ═══════════════════════════════════════════════════════════════


class TestTransformInsertInsert:
    """Test transform when both operations are Inserts."""

    def test_a_before_b(self):
        """A inserts before B → B shifts right."""
        a = Insert(0, "X")
        b = Insert(5, "Y")
        a_prime, b_prime = transform(a, b)

        # A is unchanged (it's before B)
        assert a_prime == Insert(0, "X")
        # B shifts right by len("X") = 1
        assert b_prime == Insert(6, "Y")

    def test_b_before_a(self):
        """B inserts before A → A shifts right."""
        a = Insert(5, "X")
        b = Insert(0, "Y")
        a_prime, b_prime = transform(a, b)

        # A shifts right by len("Y") = 1
        assert a_prime == Insert(6, "X")
        # B is unchanged
        assert b_prime == Insert(0, "Y")

    def test_same_position_a_wins(self):
        """Same position → A wins (convention), B shifts."""
        a = Insert(3, "X")
        b = Insert(3, "Y")
        a_prime, b_prime = transform(a, b)

        assert a_prime == Insert(3, "X")
        assert b_prime == Insert(4, "Y")  # shifted by len("X")

    def test_convergence_a_before_b(self):
        """THE KEY TEST: Both paths must produce the same result."""
        doc = "Hello World"
        a = Insert(0, "X")
        b = Insert(5, "Y")
        a_prime, b_prime = transform(a, b)

        # Path 1: apply a, then b'
        path1 = apply(apply(doc, a), b_prime)
        # Path 2: apply b, then a'
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2

    def test_convergence_same_position(self):
        doc = "Hello"
        a = Insert(3, "XX")
        b = Insert(3, "YY")
        a_prime, b_prime = transform(a, b)

        path1 = apply(apply(doc, a), b_prime)
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2

    def test_convergence_multi_char_inserts(self):
        doc = "ABCDE"
        a = Insert(2, "xyz")
        b = Insert(4, "123")
        a_prime, b_prime = transform(a, b)

        path1 = apply(apply(doc, a), b_prime)
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2


# ═══════════════════════════════════════════════════════════════
# TEST GROUP 4: Transform — Insert vs Delete
# ═══════════════════════════════════════════════════════════════


class TestTransformInsertDelete:
    """Test transform when one op is Insert and the other is Delete."""

    def test_insert_before_delete(self):
        """Insert is before the delete region."""
        ins = Insert(1, "X")
        dlt = Delete(3, 2)
        ins_prime, dlt_prime = transform(ins, dlt)

        assert ins_prime == Insert(1, "X")          # unchanged
        assert dlt_prime == Delete(4, 2)             # shifted right by 1

    def test_insert_after_delete(self):
        """Insert is after the delete region."""
        ins = Insert(5, "X")
        dlt = Delete(1, 2)
        ins_prime, dlt_prime = transform(ins, dlt)

        assert ins_prime == Insert(3, "X")           # shifted left by 2
        assert dlt_prime == Delete(1, 2)             # unchanged

    def test_insert_inside_delete(self):
        """Insert falls inside the delete range — THE TRICKY CASE."""
        ins = Insert(3, "XY")
        dlt = Delete(2, 4)
        ins_prime, dlt_prime = transform(ins, dlt)

        # Insert is swallowed by the delete
        assert isinstance(ins_prime, NoOp)
        # Delete expands to include the inserted text
        assert dlt_prime == Delete(2, 6)  # 4 + len("XY")

    def test_insert_at_delete_start(self):
        """Insert at exactly the delete start position."""
        ins = Insert(2, "X")
        dlt = Delete(2, 3)
        ins_prime, dlt_prime = transform(ins, dlt)

        # ins.pos <= dlt.pos → Case 2a
        assert ins_prime == Insert(2, "X")
        assert dlt_prime == Delete(3, 3)  # shifted right by 1

    def test_convergence_insert_before_delete(self):
        doc = "ABCDE"
        ins = Insert(1, "X")
        dlt = Delete(3, 2)
        ins_prime, dlt_prime = transform(ins, dlt)

        path1 = apply(apply(doc, ins), dlt_prime)
        path2 = apply(apply(doc, dlt), ins_prime)

        assert path1 == path2

    def test_convergence_insert_after_delete(self):
        doc = "ABCDE"
        ins = Insert(4, "XY")
        dlt = Delete(1, 2)
        ins_prime, dlt_prime = transform(ins, dlt)

        path1 = apply(apply(doc, ins), dlt_prime)
        path2 = apply(apply(doc, dlt), ins_prime)

        assert path1 == path2

    def test_convergence_insert_inside_delete(self):
        doc = "ABCDEFGH"
        ins = Insert(4, "XY")
        dlt = Delete(2, 5)
        ins_prime, dlt_prime = transform(ins, dlt)

        path1 = apply(apply(doc, ins), dlt_prime)
        path2 = apply(apply(doc, dlt), ins_prime)

        assert path1 == path2


# ═══════════════════════════════════════════════════════════════
# TEST GROUP 5: Transform — Delete vs Insert (Mirror)
# ═══════════════════════════════════════════════════════════════


class TestTransformDeleteInsert:
    """Test transform when A is Delete and B is Insert (mirror of InsertDelete)."""

    def test_delete_before_insert(self):
        dlt = Delete(1, 2)
        ins = Insert(5, "X")
        dlt_prime, ins_prime = transform(dlt, ins)

        assert dlt_prime == Delete(1, 2)             # unchanged
        assert ins_prime == Insert(3, "X")           # shifted left by 2

    def test_convergence_delete_vs_insert(self):
        doc = "ABCDEFGH"
        dlt = Delete(2, 3)
        ins = Insert(6, "XY")
        dlt_prime, ins_prime = transform(dlt, ins)

        path1 = apply(apply(doc, dlt), ins_prime)
        path2 = apply(apply(doc, ins), dlt_prime)

        assert path1 == path2


# ═══════════════════════════════════════════════════════════════
# TEST GROUP 6: Transform — Delete vs Delete
# ═══════════════════════════════════════════════════════════════


class TestTransformDeleteDelete:
    """Test transform when both operations are Deletes — including overlaps."""

    def test_no_overlap_a_before_b(self):
        """A deletes before B, no overlap."""
        a = Delete(0, 2)   # delete positions 0,1
        b = Delete(5, 3)   # delete positions 5,6,7
        a_prime, b_prime = transform(a, b)

        assert a_prime == Delete(0, 2)
        assert b_prime == Delete(3, 3)  # shifted left by 2

    def test_no_overlap_b_before_a(self):
        """B deletes before A, no overlap."""
        a = Delete(5, 2)
        b = Delete(0, 3)
        a_prime, b_prime = transform(a, b)

        assert a_prime == Delete(2, 2)  # shifted left by 3
        assert b_prime == Delete(0, 3)

    def test_identical_deletes(self):
        """Both users delete the exact same range → both become no-ops."""
        a = Delete(2, 3)
        b = Delete(2, 3)
        a_prime, b_prime = transform(a, b)

        assert isinstance(a_prime, NoOp)
        assert isinstance(b_prime, NoOp)

    def test_a_contains_b(self):
        """A's delete fully contains B's delete."""
        a = Delete(1, 6)   # positions 1-6
        b = Delete(2, 3)   # positions 2-4 (inside A)
        a_prime, b_prime = transform(a, b)

        # B is fully contained in A → B becomes no-op
        assert isinstance(b_prime, NoOp)
        # A still needs to delete the parts B didn't cover
        assert isinstance(a_prime, Delete)
        assert a_prime.count == 3  # 6 - 3 = 3

    def test_b_contains_a(self):
        """B's delete fully contains A's delete."""
        a = Delete(3, 2)   # positions 3-4
        b = Delete(1, 6)   # positions 1-6 (contains A)
        a_prime, b_prime = transform(a, b)

        assert isinstance(a_prime, NoOp)
        assert isinstance(b_prime, Delete)
        assert b_prime.count == 4  # 6 - 2 = 4

    def test_partial_overlap(self):
        """Partial overlap between two deletes."""
        a = Delete(2, 4)   # positions 2-5
        b = Delete(4, 4)   # positions 4-7
        a_prime, b_prime = transform(a, b)

        # Overlap is positions 4-5 (count=2)
        # A' needs to delete its non-overlapping part: 2 chars (pos 2-3)
        assert isinstance(a_prime, Delete)
        assert a_prime.count == 2
        # B' needs to delete its non-overlapping part: 2 chars (pos 6-7)
        assert isinstance(b_prime, Delete)
        assert b_prime.count == 2

    def test_convergence_no_overlap(self):
        doc = "ABCDEFGHIJ"
        a = Delete(0, 3)
        b = Delete(7, 3)
        a_prime, b_prime = transform(a, b)

        path1 = apply(apply(doc, a), b_prime)
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2

    def test_convergence_identical(self):
        doc = "ABCDEFGH"
        a = Delete(2, 4)
        b = Delete(2, 4)
        a_prime, b_prime = transform(a, b)

        path1 = apply(apply(doc, a), b_prime)
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2

    def test_convergence_partial_overlap(self):
        doc = "ABCDEFGHIJ"
        a = Delete(2, 4)   # delete CDEF
        b = Delete(4, 4)   # delete EFGH
        a_prime, b_prime = transform(a, b)

        path1 = apply(apply(doc, a), b_prime)
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2

    def test_convergence_a_contains_b(self):
        doc = "ABCDEFGHIJ"
        a = Delete(1, 8)
        b = Delete(3, 4)
        a_prime, b_prime = transform(a, b)

        path1 = apply(apply(doc, a), b_prime)
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2


# ═══════════════════════════════════════════════════════════════
# TEST GROUP 7: The Ultimate Convergence Tests
# ═══════════════════════════════════════════════════════════════
# These tests verify the diamond property with realistic scenarios.


class TestConvergence:
    """End-to-end convergence tests simulating real editing scenarios."""

    def test_two_users_typing_at_different_positions(self):
        """User A types at the start, User B types at the end."""
        doc = "Hello World"
        a = Insert(0, "Dear ")          # "Dear Hello World"
        b = Insert(11, "!")              # "Hello World!"
        a_prime, b_prime = transform(a, b)

        path1 = apply(apply(doc, a), b_prime)
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2
        assert path1 == "Dear Hello World!"

    def test_user_types_while_other_deletes(self):
        """User A inserts text, User B deletes different text."""
        doc = "The quick brown fox"
        a = Insert(10, "lazy ")          # "The quick lazy brown fox"
        b = Delete(14, 4)               # "The quick brow"... wait
        # Let's use cleaner positions
        doc = "ABCDEFGHIJ"
        a = Insert(3, "XYZ")            # "ABCXYZDEFGHIJ"
        b = Delete(7, 3)                # "ABCDEFGJ"  (deleted "HIJ"... wait)
        # Delete positions 7,8,9 = "HIJ"
        a_prime, b_prime = transform(a, b)

        path1 = apply(apply(doc, a), b_prime)
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2

    def test_both_users_delete_different_parts(self):
        """Both users delete different parts of the document."""
        doc = "Hello Beautiful World"
        a = Delete(5, 10)    # Delete " Beautiful"  → "Hello World"
        b = Delete(15, 6)    # Delete " World"      → "Hello Beautiful"
        a_prime, b_prime = transform(a, b)

        path1 = apply(apply(doc, a), b_prime)
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2
        assert path1 == "Hello"

    def test_simultaneous_typing_at_same_position(self):
        """Both users type at the exact same position (tie-break test)."""
        doc = "Hello"
        a = Insert(5, " World")
        b = Insert(5, " Everyone")
        a_prime, b_prime = transform(a, b)

        path1 = apply(apply(doc, a), b_prime)
        path2 = apply(apply(doc, b), a_prime)

        assert path1 == path2
        # A wins (goes first), so result is "Hello World Everyone"
        assert path1 == "Hello World Everyone"
