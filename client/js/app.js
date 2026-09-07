document.addEventListener("DOMContentLoaded", () => {
    const otClient = new OTClient();
    const ws = new LoomWebSocket();
    const editor = new LoomEditor("editor");
    const revisionDisplay = document.getElementById("revision-display");
    const clientIdDisplay = document.getElementById("client-id-display");
    const userCountDisplay = document.getElementById("user-count-text");
    const statusEl = document.getElementById("connection-status");
    const statusText = statusEl.querySelector(".status-text");
    const remoteCursorsContainer = document.getElementById("remote-cursors-container");
    const activeCursors = {};
    editor.onLocalEdit = (op) => {
        otClient.localEdit(op);
    };
    editor.onCursorMove = (position) => {
        ws.sendCursor(position);
    };
    otClient.onSendOperation = (op, revision) => {
        ws.sendOperation(op, revision);
    };
    otClient.onApplyRemote = (op) => {
        editor.applyRemoteOp(op);
    };
    ws.onSync = (data) => {
        otClient.reset(data.revision);
        editor.setContent(data.content);
        clientIdDisplay.textContent = `ID: ${data.your_client_id}`;
        updateRevisionDisplay(data.revision);
        updateUserCount(data.clients.length);
        data.clients.forEach(c => {
            if (c.client_id !== data.your_client_id) {
                updateRemoteCursor(c.client_id, c.position, c.color);
            }
        });
    };
    ws.onAck = (data) => {
        otClient.serverAck(data.revision);
        updateRevisionDisplay(data.revision);
    };
    ws.onRemoteOp = (data) => {
        otClient.remoteOperation(data.op, data.revision);
        updateRevisionDisplay(data.revision);
    };
    ws.onCursor = (data) => {
        updateRemoteCursor(data.client_id, data.position, data.color);
    };
    ws.onPresence = (data) => {
        let count = parseInt(userCountDisplay.textContent);
        if (data.action === "join") count++;
        if (data.action === "leave") {
            count = Math.max(0, count - 1);
            removeRemoteCursor(data.client_id);
        }
        updateUserCount(count);
    };
    ws.onStatusChange = (status) => {
        statusEl.className = `status ${status}`;
        statusText.textContent = status.charAt(0).toUpperCase() + status.slice(1);
    };
    function updateRevisionDisplay(rev) {
        revisionDisplay.textContent = `Rev: ${rev}`;
    }
    function updateUserCount(count) {
        userCountDisplay.textContent = count;
    }
    function updateRemoteCursor(clientId, position, color) {
        if (!position && position !== 0) return;
        let cursor = activeCursors[clientId];
        if (!cursor) {
            const el = document.createElement("div");
            el.className = "remote-cursor-label";
            el.style.backgroundColor = color;
            el.textContent = clientId;
            remoteCursorsContainer.appendChild(el);
            cursor = { element: el, timeout: null };
            activeCursors[clientId] = cursor;
        }
        cursor.element.style.opacity = "1";
        cursor.element.style.top = `${60 + (Object.keys(activeCursors).indexOf(clientId) * 30)}px`;
        cursor.element.style.right = "40px";
        if (cursor.timeout) clearTimeout(cursor.timeout);
        cursor.timeout = setTimeout(() => {
            if (cursor.element) cursor.element.style.opacity = "0.3";
        }, 2000);
    }
    function removeRemoteCursor(clientId) {
        if (activeCursors[clientId]) {
            if (activeCursors[clientId].timeout) {
                clearTimeout(activeCursors[clientId].timeout);
            }
            activeCursors[clientId].element.remove();
            delete activeCursors[clientId];
        }
    }
    ws.connect();
});
