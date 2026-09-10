document.addEventListener("DOMContentLoaded", () => {
    const auth = new LoomAuth();
    const otClient = new OTClient();
    let currentDocumentId = null;
    const ws = new LoomWebSocket(() => auth.fetchWsTicket(currentDocumentId));
    const editor = new LoomEditor("editor");
    const dashboard = new LoomDashboard(auth, document.getElementById("documents-list"));

    const revisionDisplay = document.getElementById("revision-display");
    const clientIdDisplay = document.getElementById("client-id-display");
    const userCountDisplay = document.getElementById("user-count-text");
    const statusEl = document.getElementById("connection-status");
    const statusText = statusEl.querySelector(".status-text");
    const remoteCursorsContainer = document.getElementById("remote-cursors-container");
    const docTitleDisplay = document.getElementById("doc-title");
    let activeCursors = {};

    const authScreen = document.getElementById("auth-screen");
    const dashboardScreen = document.getElementById("dashboard-screen");
    const header = document.getElementById("header");
    const editorContainer = document.getElementById("editor-container");
    const footer = document.getElementById("footer");
    const currentUsernameDisplay = document.getElementById("current-username");
    const logoutBtn = document.getElementById("logout-btn");
    const dashboardUsernameDisplay = document.getElementById("dashboard-username");
    const dashboardLogoutBtn = document.getElementById("dashboard-logout-btn");
    const backToDashboardLink = document.getElementById("back-to-dashboard");

    const loginForm = document.getElementById("login-form");
    const signupForm = document.getElementById("signup-form");
    const loginError = document.getElementById("login-error");
    const signupError = document.getElementById("signup-error");
    const showSignupLink = document.getElementById("show-signup");
    const showLoginLink = document.getElementById("show-login");

    const createDocumentForm = document.getElementById("create-document-form");
    const newDocumentTitleInput = document.getElementById("new-document-title");
    const dashboardError = document.getElementById("dashboard-error");

    const shareModal = document.getElementById("share-modal");
    const shareModalTitle = document.getElementById("share-modal-title");
    const shareForm = document.getElementById("share-form");
    const shareUsernameInput = document.getElementById("share-username");
    const shareRoleSelect = document.getElementById("share-role");
    const shareError = document.getElementById("share-error");
    const shareMembersList = document.getElementById("share-members-list");
    const shareModalClose = document.getElementById("share-modal-close");
    let shareModalDocumentId = null;

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
        editor.textarea.readOnly = data.your_role === "viewer";
        docTitleDisplay.textContent = data.title || "Untitled Document";
        clientIdDisplay.textContent = `ID: ${data.your_client_id}`;
        updateRevisionDisplay(data.revision);
        updateUserCount(data.clients.length);
        data.clients.forEach(c => {
            if (c.client_id !== data.your_client_id) {
                updateRemoteCursor(c.client_id, c.cursor_pos, c.color);
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
    ws.onDocumentDeleted = () => {
        alert("This document was deleted.");
        goToDashboard();
    };
    ws.onError = (data) => {
        // Surfaces server-side rejections (e.g. a viewer trying to edit).
        console.warn("[Server error]", data.message);
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
    function clearRemoteCursors() {
        Object.values(activeCursors).forEach(c => {
            if (c.timeout) clearTimeout(c.timeout);
            c.element.remove();
        });
        activeCursors = {};
    }

    // ---- Screen routing -----------------------------------------------

    function hideAllScreens() {
        authScreen.classList.add("hidden");
        dashboardScreen.classList.add("hidden");
        header.classList.add("hidden");
        editorContainer.classList.add("hidden");
        footer.classList.add("hidden");
    }

    function showAuthScreen() {
        hideAllScreens();
        authScreen.classList.remove("hidden");
        location.hash = "";
    }

    async function goToDashboard() {
        ws.disconnect();
        currentDocumentId = null;
        hideAllScreens();
        dashboardScreen.classList.remove("hidden");
        dashboardUsernameDisplay.textContent = auth.username;
        location.hash = "#/documents";
        dashboardError.textContent = "";
        try {
            await dashboard.load();
        } catch (err) {
            dashboardError.textContent = err.message;
        }
    }

    function openDocument(documentId) {
        currentDocumentId = documentId;
        otClient.reset(0);
        editor.setContent("");
        clearRemoteCursors();
        hideAllScreens();
        header.classList.remove("hidden");
        editorContainer.classList.remove("hidden");
        footer.classList.remove("hidden");
        currentUsernameDisplay.textContent = auth.username;
        location.hash = `#/documents/${documentId}`;
        ws.connect();
    }

    function routeAfterAuth() {
        const match = location.hash.match(/^#\/documents\/(.+)$/);
        if (match) {
            openDocument(match[1]);
        } else {
            goToDashboard();
        }
    }

    // ---- Auth forms ------------------------------------------------------

    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        loginError.textContent = "";
        const username = document.getElementById("login-username").value;
        const password = document.getElementById("login-password").value;
        try {
            await auth.login(username, password);
            routeAfterAuth();
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
            routeAfterAuth();
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

    async function doLogout() {
        ws.disconnect();
        await auth.logout();
        showAuthScreen();
    }
    logoutBtn.addEventListener("click", doLogout);
    dashboardLogoutBtn.addEventListener("click", doLogout);

    backToDashboardLink.addEventListener("click", (e) => {
        e.preventDefault();
        goToDashboard();
    });

    // ---- Dashboard ---------------------------------------------------

    dashboard.onOpenDocument = (documentId) => openDocument(documentId);
    dashboard.onShareDocument = (documentId, title) => openShareModal(documentId, title);

    createDocumentForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        dashboardError.textContent = "";
        try {
            await dashboard.create(newDocumentTitleInput.value.trim());
            newDocumentTitleInput.value = "";
        } catch (err) {
            dashboardError.textContent = err.message;
        }
    });

    // ---- Share modal ---------------------------------------------------

    async function openShareModal(documentId, title) {
        shareModalDocumentId = documentId;
        shareModalTitle.textContent = `Share "${title}"`;
        shareError.textContent = "";
        shareUsernameInput.value = "";
        shareModal.classList.remove("hidden");
        await refreshShareMembers();
    }

    async function refreshShareMembers() {
        shareMembersList.innerHTML = "";
        try {
            const detail = await dashboard.getDetail(shareModalDocumentId);
            detail.members.forEach(m => {
                const row = document.createElement("div");
                row.className = "share-member-row";
                const label = document.createElement("span");
                label.textContent = `${m.username} — ${m.role}`;
                row.appendChild(label);
                if (m.role !== "owner") {
                    const removeBtn = document.createElement("button");
                    removeBtn.textContent = "Remove";
                    removeBtn.addEventListener("click", async () => {
                        try {
                            await dashboard.unshare(shareModalDocumentId, m.user_id);
                            await refreshShareMembers();
                        } catch (err) {
                            shareError.textContent = err.message;
                        }
                    });
                    row.appendChild(removeBtn);
                }
                shareMembersList.appendChild(row);
            });
        } catch (err) {
            shareError.textContent = err.message;
        }
    }

    shareForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        shareError.textContent = "";
        try {
            await dashboard.share(shareModalDocumentId, shareUsernameInput.value.trim(), shareRoleSelect.value);
            shareUsernameInput.value = "";
            await refreshShareMembers();
        } catch (err) {
            shareError.textContent = err.message;
        }
    });

    shareModalClose.addEventListener("click", () => {
        shareModal.classList.add("hidden");
        shareModalDocumentId = null;
        dashboard.load().catch(() => {});
    });

    // ---- Startup ---------------------------------------------------------

    (async () => {
        const resumed = await auth.tryResumeSession();
        if (resumed) {
            routeAfterAuth();
        } else {
            showAuthScreen();
        }
    })();
});
