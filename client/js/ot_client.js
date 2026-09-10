class OTClient {
    constructor() {
        this.state = "synchronized";
        this.pending = null;
        this.buffer = null;
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
                this.buffer = op;
                this.state = "awaitingBuffer";
                break;
            case "awaitingBuffer":
                this.buffer = op;
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
                this.pending = this.buffer;
                this.buffer = null;
                this.state = "awaitingConfirm";
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
                // Both `pending` (in flight) and `buffer` (queued locally) are concurrent
                // with `op`. Transform `op` through pending first, then through buffer.
                const [pendingPrime, opPrime1] = transform(this.pending, op);
                const [bufferPrime, opPrime2] = transform(this.buffer, opPrime1);
                this.pending = pendingPrime;
                this.buffer = bufferPrime;
                if (this.onApplyRemote) {
                    this.onApplyRemote(opPrime2);
                }
                break;
            }
        }
    }
    reset(revision) {
        this.state = "synchronized";
        this.pending = null;
        this.buffer = null;
        this.revision = revision;
    }
}
