// Thin fetch wrapper for /api/admin/*: JSON in/out, CSRF header, uniform errors.

export class ApiError extends Error {
    constructor(message, status = 0, fields = {}) {
        super(message);
        this.status = status;
        this.fields = fields;
    }
}

function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || "";
}

/**
 * api("/api/admin/events", { params: { page: 2 } })
 * api("/api/admin/events", { method: "POST", body: {...} })
 * api("/api/me/notifications", { background: true })  // a poll: doesn't count as activity
 * Resolves with parsed JSON (or null for 204). Rejects with ApiError.
 */
export async function api(path, { method = "GET", body, params, signal, background = false } = {}) {
    const url = new URL(path, window.location.origin);
    for (const [key, value] of Object.entries(params || {})) {
        if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
    }

    const options = { method, signal, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (method !== "GET") options.headers["X-CSRFToken"] = csrfToken();
    // Automatic polls must not keep an idle session alive (see the session timeout setting).
    if (background) options.headers["X-Background-Request"] = "1";
    if (body !== undefined) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
    }

    return send(url, options);
}

/** Upload files: upload("/api/admin/assistant/avatar", { avatar: file }). */
export async function upload(path, files, { method = "POST" } = {}) {
    const body = new FormData();
    for (const [name, file] of Object.entries(files)) body.append(name, file);
    return send(new URL(path, window.location.origin), {
        method, body, credentials: "same-origin",
        headers: { Accept: "application/json", "X-CSRFToken": csrfToken() },
    });
}

async function send(url, options) {
    let response;
    try {
        response = await fetch(url, options);
    } catch (error) {
        if (error.name === "AbortError") throw error;
        throw new ApiError("Network error. Check your connection and try again.");
    }

    if (response.status === 401) {
        const next = window.location.pathname + window.location.search;
        window.location.href = "/auth/login?next=" + encodeURIComponent(next);
        throw new ApiError("Your session has expired. Please sign in again.", 401);
    }

    let data = null;
    if (response.status !== 204) {
        try { data = await response.json(); } catch { data = null; }
    }

    if (!response.ok) {
        throw new ApiError(data?.error || `Request failed (${response.status}).`, response.status, data?.fields || {});
    }
    return data;
}
