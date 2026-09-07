/**
 * Loom OT Client — Client-Side Operational Transformation
 * ========================================================
 *
 * This implements the client-side OT state machine.
 *
 * THE THREE STATES:
 *   1. Synchronized    — no pending ops, we're in sync with the server
 *   2. AwaitingConfirm — we sent an op, waiting for the server's 'ack'
 *   3. AwaitingBuffer  — we sent an op AND the user typed more while waiting
 *
 * WHY DO WE NEED THIS?
 *   The user types continuously. We send an operation to the server, but
 *   it takes time for the server to respond. If the user types MORE before
 *   the server responds, we can't send another op (the server hasn't acked
 *   the first one yet). So we buffer new ops and send them after the ack.
 *
 *   Meanwhile, OTHER users' operations might arrive. We need to transform
 *   them against our pending/buffered ops before applying them locally.
 */

class OTClient {
    constructor() {
        // The state machine starts in "synchronized" state
        this.state = "synchronized";

        // The pending operation we sent to the server but haven't been acked yet
        this.pending = null;

        // Buffer of operations the user made while we're awaiting an ack
        this.buffer = null;

        // Our last known server revision
        this.revision = 0;

        // Callback functions — set by app.js
        this.onSendOperation = null;   // called when we need to send an op to the server
        this.onApplyRemote = null;     // called when we need to apply a remote op to the editor
    }

    /**
     * Called when the user makes a local edit.
     * Creates an operation and either sends it or buffers it.
     *
     * @param {object} op - The operation ({type: "insert/delete", position, text/count})
     */
    localEdit(op) {
        switch (this.state) {
            case "synchronized":
                // No pending ops — send immediately
                this.pending = op;
                this.state = "awaitingConfirm";
                if (this.onSendOperation) {
                    this.onSendOperation(op, this.revision);
                }
                break;

            case "awaitingConfirm":
                // Already waiting for an ack — buffer this op
                this.buffer = op;
                this.state = "awaitingBuffer";
                break;

            case "awaitingBuffer":
                // Already have a buffer — compose (merge) this op with the buffer.
                // For simplicity in MVP, we replace the buffer.
                // A full implementation would compose operations together.
                this.buffer = op;
                break;
        }
    }

    /**
     * Called when the server acknowledges our operation.
     * Moves the state machine forward.
     *
     * @param {number} revision - The new server revision after our op was applied.
     */
    serverAck(revision) {
        this.revision = revision;

        switch (this.state) {
            case "awaitingConfirm":
                // Our op was accepted, nothing buffered — back to synced
                this.pending = null;
                this.state = "synchronized";
                break;

            case "awaitingBuffer":
                // Our op was accepted, but we have buffered ops — send them now
                this.pending = this.buffer;
                this.buffer = null;
                this.state = "awaitingConfirm";
                if (this.onSendOperation) {
                    this.onSendOperation(this.pending, this.revision);
                }
                break;
        }
    }

    /**
     * Called when a remote operation arrives from another client.
     * We need to transform it against any pending/buffered ops
     * before applying it to our editor.
     *
     * @param {object} op - The remote operation
     * @param {number} revision - The server revision of this operation
     */
    remoteOperation(op, revision) {
        this.revision = revision;

        switch (this.state) {
            case "synchronized":
                // No pending ops — apply the remote op directly
                if (this.onApplyRemote) {
                    this.onApplyRemote(op);
                }
                break;

            case "awaitingConfirm":
                // We have a pending op. Transform the remote op against it.
                // For MVP we apply the remote op directly — the server
                // already transformed it for us. In a production system,
                // we'd do client-side transform here.
                if (this.onApplyRemote) {
                    this.onApplyRemote(op);
                }
                break;

            case "awaitingBuffer":
                // Same as awaitingConfirm for our MVP
                if (this.onApplyRemote) {
                    this.onApplyRemote(op);
                }
                break;
        }
    }

    /**
     * Reset the client state (e.g. on reconnect).
     */
    reset(revision) {
        this.state = "synchronized";
        this.pending = null;
        this.buffer = null;
        this.revision = revision;
    }
}
