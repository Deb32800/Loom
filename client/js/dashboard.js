class LoomDashboard {
    constructor(auth, container) {
        this.auth = auth;
        this.container = container;
        this.documents = [];
        this.onOpenDocument = null;   // (documentId) => void
        this.onShareDocument = null;  // (documentId, title) => void
    }

    async _api(path, options = {}) {
        const res = await fetch(path, {
            ...options,
            headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${this.auth.accessToken}`,
                ...(options.headers || {}),
            },
        });
        if (!res.ok) {
            let detail = `Request failed (${res.status})`;
            try {
                const err = await res.json();
                if (err.detail) detail = err.detail;
            } catch (e) {}
            throw new Error(detail);
        }
        if (res.status === 204) return null;
        return res.json();
    }

    async load() {
        this.documents = await this._api("/api/documents");
        this.render();
    }

    async create(title) {
        await this._api("/api/documents", {
            method: "POST",
            body: JSON.stringify({ title: title || "Untitled Document" }),
        });
        await this.load();
    }

    async rename(documentId, title) {
        await this._api(`/api/documents/${documentId}`, {
            method: "PATCH",
            body: JSON.stringify({ title }),
        });
        await this.load();
    }

    async remove(documentId) {
        await this._api(`/api/documents/${documentId}`, { method: "DELETE" });
        await this.load();
    }

    async getDetail(documentId) {
        return this._api(`/api/documents/${documentId}`);
    }

    async share(documentId, username, role) {
        return this._api(`/api/documents/${documentId}/share`, {
            method: "POST",
            body: JSON.stringify({ username, role }),
        });
    }

    async unshare(documentId, userId) {
        await this._api(`/api/documents/${documentId}/share/${userId}`, { method: "DELETE" });
    }

    render() {
        this.container.innerHTML = "";
        if (this.documents.length === 0) {
            const empty = document.createElement("p");
            empty.className = "dashboard-empty";
            empty.textContent = "No documents yet — create one to get started.";
            this.container.appendChild(empty);
            return;
        }
        for (const doc of this.documents) {
            this.container.appendChild(this._renderRow(doc));
        }
    }

    _renderRow(doc) {
        const row = document.createElement("div");
        row.className = "doc-row";

        const info = document.createElement("div");
        info.className = "doc-row-info";
        const title = document.createElement("span");
        title.className = "doc-row-title";
        title.textContent = doc.title;
        const badge = document.createElement("span");
        badge.className = `role-badge role-${doc.role}`;
        badge.textContent = doc.role;
        info.appendChild(title);
        info.appendChild(badge);

        const actions = document.createElement("div");
        actions.className = "doc-row-actions";

        const openBtn = document.createElement("button");
        openBtn.textContent = "Open";
        openBtn.addEventListener("click", () => {
            if (this.onOpenDocument) this.onOpenDocument(doc.id);
        });
        actions.appendChild(openBtn);

        if (doc.role === "owner" || doc.role === "editor") {
            const renameBtn = document.createElement("button");
            renameBtn.textContent = "Rename";
            renameBtn.addEventListener("click", async () => {
                const newTitle = window.prompt("Rename document", doc.title);
                if (newTitle && newTitle.trim()) {
                    try {
                        await this.rename(doc.id, newTitle.trim());
                    } catch (e) {
                        alert(e.message);
                    }
                }
            });
            actions.appendChild(renameBtn);
        }

        if (doc.role === "owner") {
            const shareBtn = document.createElement("button");
            shareBtn.textContent = "Share";
            shareBtn.addEventListener("click", () => {
                if (this.onShareDocument) this.onShareDocument(doc.id, doc.title);
            });
            actions.appendChild(shareBtn);

            const deleteBtn = document.createElement("button");
            deleteBtn.textContent = "Delete";
            deleteBtn.className = "danger";
            deleteBtn.addEventListener("click", async () => {
                if (window.confirm(`Delete "${doc.title}"? This can't be undone.`)) {
                    try {
                        await this.remove(doc.id);
                    } catch (e) {
                        alert(e.message);
                    }
                }
            });
            actions.appendChild(deleteBtn);
        }

        row.appendChild(info);
        row.appendChild(actions);
        return row;
    }
}
