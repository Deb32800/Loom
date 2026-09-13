class OTClient {
    constructor() {
        this.state = "synchronized";
        this.pending = null;
        this.buffer = [];   // queue of not-yet-sent local ops, oldest first
        this.revision = 0;
        this.onSendOperation = null;
        this.onApplyRemote = null;
    }
    localEdit(op) {
        switch (this.state) {
            case "synchronized":
                this.pending = op;
                this.state = "awaitingConfirm";
                if (this.onSendOperation) {
                    this.onSendOperation(op, this.revision);
                }
                break;
            case "awaitingConfirm":
                this.buffer.push(op);
                this.state = "awaitingBuffer";
                break;
            case "awaitingBuffer":
                // Queue it rather than overwrite: typing fast enough to stack
                // up 3+ edits before the first ack returns is completely
                // normal, and overwriting here used to silently drop every
                // buffered edit but the last one from what reaches the server.
                this.buffer.push(op);
                break;
        }
    }
    serverAck(revision) {
        this.revision = revision;
        switch (this.state) {
            case "awaitingConfirm":
                this.pending = null;
                this.state = "synchronized";
                break;
            case "awaitingBuffer":
                this.pending = this.buffer.shift();
                // More still queued after this one -> stay in awaitingBuffer;
                // otherwise this was the last of them -> back to awaitingConfirm.
                this.state = this.buffer.length > 0 ? "awaitingBuffer" : "awaitingConfirm";
                if (this.onSendOperation) {
                    this.onSendOperation(this.pending, this.revision);
                }
                break;
        }
    }
    remoteOperation(op, revision) {
        this.revision = revision;
        switch (this.state) {
            case "synchronized":
                if (this.onApplyRemote) {
                    this.onApplyRemote(op);
                }
                break;
            case "awaitingConfirm": {
                // Our `pending` op hasn't been acked yet, so it's concurrent with `op`.
                // Transform both against each other: pending is updated to account for
                // `op`, and the transformed `op'` (not `op`) is what gets applied locally,
                // since our local buffer already reflects `pending`.
                const [pendingPrime, opPrime] = transform(this.pending, op);
                this.pending = pendingPrime;
                if (this.onApplyRemote) {
                    this.onApplyRemote(opPrime);
                }
                break;
            }
            case "awaitingBuffer": {
                // `pending` (in flight) and every op still queued in `buffer` are all
                // concurrent with `op`. Transform `op` through pending first, then
                // through each queued op in turn, updating each of them to account
                // for `op` along the way.
                const [pendingPrime, opPrime1] = transform(this.pending, op);
                this.pending = pendingPrime;
                let opPrime = opPrime1;
                const newBuffer = [];
                for (const queuedOp of this.buffer) {
                    const [queuedPrime, nextOpPrime] = transform(queuedOp, opPrime);
                    newBuffer.push(queuedPrime);
                    opPrime = nextOpPrime;
                }
                this.buffer = newBuffer;
                if (this.onApplyRemote) {
                    this.onApplyRemote(opPrime);
                }
                break;
            }
        }
    }
    reset(revision) {
        this.state = "synchronized";
        this.pending = null;
        this.buffer = [];
        this.revision = revision;
    }
}
