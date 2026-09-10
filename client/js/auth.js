class LoomAuth {
    constructor() {
        this.accessToken = null;
        this.username = null;
    }

    isAuthenticated() {
        return !!this.accessToken;
    }

    async signup(username, password) {
        return this._credentialsRequest("/api/auth/signup", username, password);
    }

    async login(username, password) {
        return this._credentialsRequest("/api/auth/login", username, password);
    }

    // Uses the httpOnly refresh cookie (if any) to silently resume a session
    // without asking the user to log in again.
    async tryResumeSession() {
        try {
            const res = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
            if (!res.ok) return false;
            const data = await res.json();
            this.accessToken = data.access_token;
            this.username = data.username;
            return true;
        } catch (e) {
            return false;
        }
    }

    async logout() {
        try {
            await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
        } finally {
            this.accessToken = null;
            this.username = null;
        }
    }

    async fetchWsTicket(documentId) {
        const res = await fetch(`/api/documents/${documentId}/ws-ticket`, {
            method: "POST",
            headers: { Authorization: `Bearer ${this.accessToken}` },
        });
        if (!res.ok) throw new Error("Failed to obtain a WebSocket ticket");
        const data = await res.json();
        return data.ticket;
    }

    async _credentialsRequest(path, username, password) {
        const res = await fetch(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify({ username, password }),
        });
        if (!res.ok) {
            let detail = `Request failed (${res.status})`;
            try {
                const err = await res.json();
                if (err.detail) detail = err.detail;
            } catch (e) {}
            throw new Error(detail);
        }
        const data = await res.json();
        this.accessToken = data.access_token;
        this.username = data.username;
        return data;
    }
}
