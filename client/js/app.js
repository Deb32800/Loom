/**
 * Loom Editor — Main Application Wiring
 * =====================================
 *
 * This file wires together the UI (LoomEditor), the network (LoomWebSocket),
 * and the logic (OTClient).
 */

document.addEventListener("DOMContentLoaded", () => {
    // 1. Initialize components
    const otClient = new OTClient();
    const ws = new LoomWebSocket();
    const editor = new LoomEditor("editor");

    // UI elements
    const revisionDisplay = document.getElementById("revision-display");
    const clientIdDisplay = document.getElementById("client-id-display");
    const userCountDisplay = document.getElementById("user-count-text");
    const statusEl = document.getElementById("connection-status");
    const statusText = statusEl.querySelector(".status-text");
    const remoteCursorsContainer = document.getElementById("remote-cursors-container");

    // Track active remote cursors { clientId: { element, timeout } }
    const activeCursors = {};

    // ── Wire Editor -> OT Client ─────────────────────────────
    
    // When user types something
    editor.onLocalEdit = (op) => {
        otClient.localEdit(op);
    };

    // When user moves cursor
    editor.onCursorMove = (position) => {
        ws.sendCursor(position);
    };

    // ── Wire OT Client -> Network & Editor ───────────────────
    
    // When OT decides it's safe to send an operation to the server
    otClient.onSendOperation = (op, revision) => {
        ws.sendOperation(op, revision);
    };

    // When OT says a remote operation is ready to be applied locally
    otClient.onApplyRemote = (op) => {
        editor.applyRemoteOp(op);
    };

    // ── Wire Network -> OT Client & UI ───────────────────────
    
    // 1. Initial Sync (Server says: "Here is the document")
    ws.onSync = (data) => {
        otClient.reset(data.revision);
        editor.setContent(data.content);
        
        clientIdDisplay.textContent = `ID: ${data.your_client_id}`;
        updateRevisionDisplay(data.revision);
        updateUserCount(data.clients.length);
        
        // Render initial remote cursors
        data.clients.forEach(c => {
            if (c.client_id !== data.your_client_id) {
                updateRemoteCursor(c.client_id, c.position, c.color);
            }
        });
    };

    // 2. Server Acknowledgment (Server says: "I got your operation")
    ws.onAck = (data) => {
        otClient.serverAck(data.revision);
        updateRevisionDisplay(data.revision);
    };

    // 3. Remote Operation (Server says: "Someone else edited the document")
    ws.onRemoteOp = (data) => {
        otClient.remoteOperation(data.op, data.revision);
        updateRevisionDisplay(data.revision);
    };

    // 4. Remote Cursor Move
    ws.onCursor = (data) => {
        updateRemoteCursor(data.client_id, data.position, data.color);
    };

    // 5. User Joined or Left
    ws.onPresence = (data) => {
        // We get a simple action="join" or "leave"
        // Let's just ask for a fresh client list in a real app, 
        // but for now we'll just manually tweak the count
        let count = parseInt(userCountDisplay.textContent);
        if (data.action === "join") count++;
        if (data.action === "leave") {
            count = Math.max(0, count - 1);
            removeRemoteCursor(data.client_id);
        }
        updateUserCount(count);
    };

    // 6. Connection Status updates
    ws.onStatusChange = (status) => {
        statusEl.className = `status ${status}`;
        statusText.textContent = status.charAt(0).toUpperCase() + status.slice(1);
    };

    // ── UI Helpers ───────────────────────────────────────────

    function updateRevisionDisplay(rev) {
        revisionDisplay.textContent = `Rev: ${rev}`;
    }

    function updateUserCount(count) {
        userCountDisplay.textContent = count;
    }

    function updateRemoteCursor(clientId, position, color) {
        if (!position && position !== 0) return;

        // Try to figure out where the cursor is on screen
        // (This is hard with just a textarea, we'd normally use CodeMirror)
        // For MVP, we'll just show the user ID next to a blinking line
        
        // Let's just create a simple floating badge for now
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
        
        // A real app maps the string index (position) to X/Y coordinates
        // Since we are using a plain textarea for MVP, calculating exact X/Y
        // is complex. We'll just show it in the top right to prove we got it.
        // If we switch to CodeMirror 6 in Phase 6, this becomes trivial!
        
        // Show the badge, hide after 2 seconds of inactivity
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

    // ── Start the app ────────────────────────────────────────
    
    // Connect to the WebSocket!
    ws.connect();
});
