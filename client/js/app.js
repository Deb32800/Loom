document.addEventListener("DOMContentLoaded", () => {
    const auth = new LoomAuth();
    const otClient = new OTClient();
    const ws = new LoomWebSocket(() => auth.fetchWsTicket());
    const editor = new LoomEditor("editor");

    const revisionDisplay = document.getElementById("revision-display");
    const clientIdDisplay = document.getElementById("client-id-display");
    const userCountDisplay = document.getElementById("user-count-text");
    const statusEl = document.getElementById("connection-status");
    const statusText = statusEl.querySelector(".status-text");
    const remoteCursorsContainer = document.getElementById("remote-cursors-container");
    const activeCursors = {};

    const authScreen = document.getElementById("auth-screen");
    const header = document.getElementById("header");
    const editorContainer = document.getElementById("editor-container");
    const footer = document.getElementById("footer");
    const currentUsernameDisplay = document.getElementById("current-username");
    const logoutBtn = document.getElementById("logout-btn");

    const loginForm = document.getElementById("login-form");
    const signupForm = document.getElementById("signup-form");
    const loginError = document.getElementById("login-error");
    const signupError = document.getElementById("signup-error");
    const showSignupLink = document.getElementById("show-signup");
    const showLoginLink = document.getElementById("show-login");

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

    function showEditorScreen() {
        authScreen.classList.add("hidden");
        header.classList.remove("hidden");
        editorContainer.classList.remove("hidden");
        footer.classList.remove("hidden");
        currentUsernameDisplay.textContent = auth.username;
        ws.connect();
    }
    function showAuthScreen() {
        authScreen.classList.remove("hidden");
        header.classList.add("hidden");
        editorContainer.classList.add("hidden");
        footer.classList.add("hidden");
    }

    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        loginError.textContent = "";
        const username = document.getElementById("login-username").value;
        const password = document.getElementById("login-password").value;
        try {
            await auth.login(username, password);
            showEditorScreen();
        } catch (err) {
            loginError.textContent = err.message;
        }
    });

    signupForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        signupError.textContent = "";
        const username = document.getElementById("signup-username").value;
        const password = document.getElementById("signup-password").value;
        try {
            await auth.signup(username, password);
            showEditorScreen();
        } catch (err) {
            signupError.textContent = err.message;
        }
    });

    showSignupLink.addEventListener("click", (e) => {
        e.preventDefault();
        loginForm.classList.add("hidden");
        signupForm.classList.remove("hidden");
        showSignupLink.classList.add("hidden");
        showLoginLink.classList.remove("hidden");
    });
    showLoginLink.addEventListener("click", (e) => {
        e.preventDefault();
        signupForm.classList.add("hidden");
        loginForm.classList.remove("hidden");
        showLoginLink.classList.add("hidden");
        showSignupLink.classList.remove("hidden");
    });

    logoutBtn.addEventListener("click", async () => {
        if (ws.ws) ws.ws.close();
        await auth.logout();
        showAuthScreen();
    });

    (async () => {
        const resumed = await auth.tryResumeSession();
        if (resumed) {
            showEditorScreen();
        } else {
            showAuthScreen();
        }
    })();
});
