class LoomEditor {
    constructor(textareaId) {
        this.textarea = document.getElementById(textareaId);
        this.previousValue = "";
        this._isRemoteUpdate = false;
        this.onLocalEdit = null;
        this.onCursorMove = null;
        this._setupListeners();
    }
    setContent(content) {
        this._isRemoteUpdate = true;
        this.textarea.value = content;
        this.previousValue = content;
        this._isRemoteUpdate = false;
        this._updateCharCount();
    }
    applyRemoteOp(op) {
        this._isRemoteUpdate = true;
        const cursorPos = this.textarea.selectionStart;
        const value = this.textarea.value;
        let newValue;
        let newCursorPos = cursorPos;
        if (op.type === "insert") {
            newValue = value.slice(0, op.position) + op.text + value.slice(op.position);
            if (op.position <= cursorPos) {
                newCursorPos += op.text.length;
            }
        } else if (op.type === "delete") {
            newValue = value.slice(0, op.position) + value.slice(op.position + op.count);
            if (op.position < cursorPos) {
                newCursorPos = Math.max(op.position, cursorPos - op.count);
            }
        } else {
            this._isRemoteUpdate = false;
            return;
        }
        this.textarea.value = newValue;
        this.previousValue = newValue;
        this.textarea.setSelectionRange(newCursorPos, newCursorPos);
        this._isRemoteUpdate = false;
        this._updateCharCount();
    }
    _setupListeners() {
        this.textarea.addEventListener("input", () => {
            if (this._isRemoteUpdate) return;
            const newValue = this.textarea.value;
            const ops = this._computeDiff(this.previousValue, newValue);
            this.previousValue = newValue;
            if (this.onLocalEdit) {
                for (const op of ops) {
                    this.onLocalEdit(op);
                }
            }
            this._updateCharCount();
        });
        this.textarea.addEventListener("keyup", () => this._reportCursor());
        this.textarea.addEventListener("click", () => this._reportCursor());
        this.textarea.addEventListener("select", () => this._reportCursor());
    }
    _computeDiff(oldText, newText) {
        if (oldText === newText) return [];
        let start = 0;
        while (start < oldText.length && start < newText.length && oldText[start] === newText[start]) {
            start++;
        }
        let oldEnd = oldText.length;
        let newEnd = newText.length;
        while (oldEnd > start && newEnd > start && oldText[oldEnd - 1] === newText[newEnd - 1]) {
            oldEnd--;
            newEnd--;
        }
        const deletedCount = oldEnd - start;
        const insertedText = newText.slice(start, newEnd);
        const ops = [];
        // Delete first, then insert at the same position — this mirrors what actually
        // happened (the selected range was removed, then the new text was typed in its
        // place) and keeps both ops meaningful on their own instead of losing the delete.
        if (deletedCount > 0) {
            ops.push({ type: "delete", position: start, count: deletedCount });
        }
        if (insertedText.length > 0) {
            ops.push({ type: "insert", position: start, text: insertedText });
        }
        return ops;
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
