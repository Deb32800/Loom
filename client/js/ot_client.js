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
            case "awaitingConfirm":
                if (this.onApplyRemote) {
                    this.onApplyRemote(op);
                }
                break;
            case "awaitingBuffer":
                if (this.onApplyRemote) {
                    this.onApplyRemote(op);
                }
                break;
        }
    }
    reset(revision) {
        this.state = "synchronized";
        this.pending = null;
        this.buffer = null;
        this.revision = revision;
    }
}
