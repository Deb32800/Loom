/**
 * Loom WebSocket Client — Connection Management
 * ================================================
 *
 * Handles the WebSocket connection to the server.
 * Includes automatic reconnection with exponential backoff.
 *
 * Exponential backoff means: if the connection fails, we wait
 * 1 second, then 2 seconds, then 4, then 8... up to a max.
 * This prevents hammering the server when it's down.
 */

class LoomWebSocket {
    constructor() {
        this.ws = null;
        this.clientId = null;
        this.isConnected = false;

        // Reconnection settings
        this._reconnectAttempts = 0;
        this._maxReconnectDelay = 30000; // 30 seconds max
        this._baseDelay = 1000;          // start at 1 second
        this._reconnectTimer = null;

        // Callbacks — set by app.js
        this.onSync = null;          // initial document state received
        this.onAck = null;           // server acknowledged our operation
        this.onRemoteOp = null;      // another user's operation arrived
        this.onCursor = null;        // another user's cursor moved
        this.onPresence = null;      // user joined/left
        this.onStatusChange = null;  // connection status changed
        this.onError = null;         // server sent an error
    }

    /**
     * Connect to the Loom server.
     *
     * @param {string} clientId - Optional client identifier
     */
    connect(clientId) {
        this.clientId = clientId || "user_" + Math.random().toString(36).substr(2, 8);

        // Build the WebSocket URL.
        // If we're on http://localhost:8000, the WS URL is ws://localhost:8000/ws
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${protocol}//${window.location.host}/ws?client_id=${this.clientId}`;

        this._updateStatus("connecting");

        try {
            this.ws = new WebSocket(url);

            this.ws.onopen = () => {
                this.isConnected = true;
                this._reconnectAttempts = 0;
                this._updateStatus("connected");
                console.log("[WS] Connected as", this.clientId);
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

    /**
     * Send a JSON message to the server.
     */
    send(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        }
    }

    /**
     * Send an operation to the server.
     */
    sendOperation(op, revision) {
        this.send({
            type: "operation",
            op: op,
            revision: revision,
            client_id: this.clientId
        });
    }

    /**
     * Send a cursor position update.
     */
    sendCursor(position) {
        this.send({
            type: "cursor",
            position: position,
            client_id: this.clientId
        });
    }

    // ── Internal methods ────────────────────────────────────

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
        if (this._reconnectTimer) return; // already scheduled

        // Exponential backoff: 1s, 2s, 4s, 8s, ... up to max
        const delay = Math.min(
            this._baseDelay * Math.pow(2, this._reconnectAttempts),
            this._maxReconnectDelay
        );
        this._reconnectAttempts++;

        console.log(`[WS] Reconnecting in ${delay / 1000}s (attempt ${this._reconnectAttempts})`);

        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            this.connect(this.clientId);
        }, delay);
    }
}
