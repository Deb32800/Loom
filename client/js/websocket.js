class LoomWebSocket {
    constructor() {
        this.ws = null;
        this.clientId = null;
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
    connect(clientId) {
        this.clientId = clientId || "user_" + Math.random().toString(36).substr(2, 8);
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${protocol}
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
            client_id: this.clientId
        });
    }
    sendCursor(position) {
        this.send({
            type: "cursor",
            position: position,
            client_id: this.clientId
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
            this.connect(this.clientId);
        }, delay);
    }
}
