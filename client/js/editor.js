/**
 * Loom Editor — Textarea Integration
 * =====================================
 *
 * This module bridges the textarea DOM element with the OT system.
 * It captures user edits and converts them into Insert/Delete operations.
 * It also applies remote operations to the textarea without disrupting
 * the local user's cursor position.
 *
 * HOW WE DETECT EDITS:
 *   We listen to the 'input' event on the textarea.
 *   On each input, we compare the new value to the previous value
 *   and compute a "diff" — either an insert or a delete.
 *
 *   This is a simple diff approach: find the first position where
 *   old and new text differ, and the last position. The change is
 *   either an insertion or a deletion in between.
 */

class LoomEditor {
    constructor(textareaId) {
        this.textarea = document.getElementById(textareaId);
        this.previousValue = "";

        // Set to true when we're applying a remote operation.
        // This prevents our 'input' handler from generating
        // a local op for changes we're applying from the server.
        this._isRemoteUpdate = false;

        // Callback — called with the operation when the user edits
        this.onLocalEdit = null;

        // Callback — called when the cursor moves
        this.onCursorMove = null;

        this._setupListeners();
    }

    /**
     * Initialize the editor with document content.
     */
    setContent(content) {
        this._isRemoteUpdate = true;
        this.textarea.value = content;
        this.previousValue = content;
        this._isRemoteUpdate = false;
        this._updateCharCount();
    }

    /**
     * Apply a remote operation to the textarea.
     * Preserves the local user's cursor position.
     */
    applyRemoteOp(op) {
        this._isRemoteUpdate = true;

        const cursorPos = this.textarea.selectionStart;
        const value = this.textarea.value;

        let newValue;
        let newCursorPos = cursorPos;

        if (op.type === "insert") {
            newValue = value.slice(0, op.position) + op.text + value.slice(op.position);

            // Adjust cursor: if the remote insert is before our cursor,
            // push our cursor right by the length of the inserted text.
            if (op.position <= cursorPos) {
                newCursorPos += op.text.length;
            }

        } else if (op.type === "delete") {
            newValue = value.slice(0, op.position) + value.slice(op.position + op.count);

            // Adjust cursor: if the remote delete is before our cursor,
            // pull our cursor left. But don't go past the delete position.
            if (op.position < cursorPos) {
                newCursorPos = Math.max(op.position, cursorPos - op.count);
            }

        } else {
            // noop
            this._isRemoteUpdate = false;
            return;
        }

        this.textarea.value = newValue;
        this.previousValue = newValue;

        // Restore cursor position
        this.textarea.setSelectionRange(newCursorPos, newCursorPos);

        this._isRemoteUpdate = false;
        this._updateCharCount();
    }

    // ── Internal ─────────────────────────────────────────────

    _setupListeners() {
        // Detect edits via the 'input' event
        this.textarea.addEventListener("input", () => {
            if (this._isRemoteUpdate) return;

            const newValue = this.textarea.value;
            const op = this._computeDiff(this.previousValue, newValue);
            this.previousValue = newValue;

            if (op && this.onLocalEdit) {
                this.onLocalEdit(op);
            }
            this._updateCharCount();
        });

        // Track cursor position changes
        this.textarea.addEventListener("keyup", () => this._reportCursor());
        this.textarea.addEventListener("click", () => this._reportCursor());
        this.textarea.addEventListener("select", () => this._reportCursor());
    }

    /**
     * Compute the diff between old and new text.
     * Returns a single Insert or Delete operation (or null if no change).
     */
    _computeDiff(oldText, newText) {
        if (oldText === newText) return null;

        // Find the first position where they differ (from the left)
        let start = 0;
        while (start < oldText.length && start < newText.length && oldText[start] === newText[start]) {
            start++;
        }

        // Find the first position where they differ (from the right)
        let oldEnd = oldText.length;
        let newEnd = newText.length;
        while (oldEnd > start && newEnd > start && oldText[oldEnd - 1] === newText[newEnd - 1]) {
            oldEnd--;
            newEnd--;
        }

        // Characters deleted from old text
        const deletedCount = oldEnd - start;
        // Characters inserted in new text
        const insertedText = newText.slice(start, newEnd);

        // Could be a pure delete, pure insert, or a replace (delete + insert)
        // For OT simplicity, if it's a replace, we send delete first then insert
        // But for MVP, the 'input' event usually gives us one atomic change

        if (deletedCount > 0 && insertedText.length === 0) {
            return { type: "delete", position: start, count: deletedCount };
        } else if (deletedCount === 0 && insertedText.length > 0) {
            return { type: "insert", position: start, text: insertedText };
        } else if (deletedCount > 0 && insertedText.length > 0) {
            // Replace — send as delete (the insert will come from the next diff)
            // For MVP, treat as insert at the position after delete
            // This happens with autocomplete, paste-over-selection, etc.
            // We'll just send the insert and let the server handle it
            return { type: "insert", position: start, text: insertedText };
        }

        return null;
    }

    _reportCursor() {
        if (this.onCursorMove) {
            this.onCursorMove(this.textarea.selectionStart);
        }
    }

    _updateCharCount() {
        const count = this.textarea.value.length;
        const el = document.getElementById("char-count");
        if (el) {
            el.textContent = `${count} chars`;
        }
    }
}
