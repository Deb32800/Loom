class LoomWebSocket {
    // ticketProvider: async () => ticket string. Called on every connect AND
    // every reconnect, since WS tickets are single-use — a raw ticket can't
    // just be stashed and replayed the way a client-chosen id used to be.
    constructor(ticketProvider) {
        this.ticketProvider = ticketProvider;
        this.ws = null;
        this.isConnected = false;
        this._reconnectAttempts = 0;
        this._maxReconnectDelay = 30000;
        this._baseDelay = 1000;
        this._reconnectTimer = null;
        this.onSync = null;
        this.onAck = null;
        this.onRemoteOp = null;
        this.onCursor = null;
        this.onPresence = null;
        this.onStatusChange = null;
        this.onError = null;
    }
    async connect() {
        this._updateStatus("connecting");
        let ticket;
        try {
            ticket = await this.ticketProvider();
        } catch (e) {
            console.error("[WS] Failed to obtain a connection ticket:", e);
            this._scheduleReconnect();
            return;
        }
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${protocol}//${window.location.host}/ws?ticket=${encodeURIComponent(ticket)}`;
        try {
            this.ws = new WebSocket(url);
            this.ws.onopen = () => {
                this.isConnected = true;
                this._reconnectAttempts = 0;
                this._updateStatus("connected");
                console.log("[WS] Connected");
            };
            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this._handleMessage(data);
            };
            this.ws.onclose = (event) => {
                this.isConnected = false;
                this._updateStatus("disconnected");
                console.log("[WS] Disconnected, code:", event.code);
                this._scheduleReconnect();
            };
            this.ws.onerror = (error) => {
                console.error("[WS] Error:", error);
            };
        } catch (e) {
            console.error("[WS] Failed to create WebSocket:", e);
            this._scheduleReconnect();
        }
    }
    send(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        }
    }
    sendOperation(op, revision) {
        this.send({
            type: "operation",
            op: op,
            revision: revision,
        });
    }
    sendCursor(position) {
        this.send({
            type: "cursor",
            position: position,
        });
    }
    _handleMessage(data) {
        switch (data.type) {
            case "sync":
                if (this.onSync) this.onSync(data);
                break;
            case "ack":
                if (this.onAck) this.onAck(data);
                break;
            case "operation":
                if (this.onRemoteOp) this.onRemoteOp(data);
                break;
            case "cursor":
                if (this.onCursor) this.onCursor(data);
                break;
            case "presence":
                if (this.onPresence) this.onPresence(data);
                break;
            case "error":
                console.error("[WS] Server error:", data.message);
                if (this.onError) this.onError(data);
                break;
            default:
                console.warn("[WS] Unknown message type:", data.type);
        }
    }
    _updateStatus(status) {
        if (this.onStatusChange) {
            this.onStatusChange(status);
        }
    }
    _scheduleReconnect() {
        if (this._reconnectTimer) return;
        const delay = Math.min(
            this._baseDelay * Math.pow(2, this._reconnectAttempts),
            this._maxReconnectDelay
        );
        this._reconnectAttempts++;
        console.log(`[WS] Reconnecting in ${delay / 1000}s (attempt ${this._reconnectAttempts})`);
        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            this.connect();
        }, delay);
    }
}
