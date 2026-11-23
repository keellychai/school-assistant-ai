// Shared UI helpers: DOM builder, icons, toasts, confirm dialog, form modal, dates.
// Rule: user data is only ever inserted as text (textContent), never as HTML.

import { ApiError } from "./api.js";

// ---------- DOM ----------

/** el("button", { class: "btn", onclick: fn }, "Save") — children may be strings (as text) or Nodes. */
export function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
        if (value === undefined || value === null || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "dataset") Object.assign(node.dataset, value);
        else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
        if (child === undefined || child === null || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

// Same shapes as templates/admin/_icons.html (Feather Icons, MIT).
const ICONS = {
    search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "chevron-left": '<polyline points="15 18 9 12 15 6"/>',
    "chevron-right": '<polyline points="9 18 15 12 9 6"/>',
    more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    check: '<polyline points="20 6 9 17 4 12"/>',
    "arrow-up": '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
    "arrow-down": '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>',
    trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
};

export function icon(name, size = 18) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICONS[name] || ""; // static, trusted markup only
    return svg;
}

export function debounce(fn, wait = 300) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), wait);
    };
}

// ---------- Dates (stored in UTC, shown in the configured timezone) ----------

const TIMEZONE = document.body?.dataset.timezone || undefined;

export function formatDateTime(iso, options = { dateStyle: "medium", timeStyle: "short" }) {
    if (!iso) return "—";
    try {
        return new Intl.DateTimeFormat(undefined, { ...options, timeZone: TIMEZONE }).format(new Date(iso));
    } catch {
        return new Date(iso).toLocaleString();
    }
}

export function relativeTime(iso) {
    if (!iso) return "—";
    const seconds = Math.round((new Date(iso) - Date.now()) / 1000);
    const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
    const format = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    for (const [unit, size] of units) {
        if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
    }
    return format.format(seconds, "second");
}

// ---------- Toasts ----------

export function toast(message, { type = "success", timeout = 4000 } = {}) {
    const region = document.getElementById("toast-region");
    if (!region) return;
    const close = () => {
        item.classList.add("is-leaving");
        setTimeout(() => item.remove(), 200);
    };
    const item = el("div", { class: `toast${type === "error" ? " is-error" : ""}`, role: type === "error" ? "alert" : "status" },
        el("span", {}, message),
        el("button", { class: "toast-close", type: "button", "aria-label": "Dismiss", onclick: close }, icon("x", 16)),
    );
    region.append(item);
    if (timeout) setTimeout(close, type === "error" ? timeout * 1.5 : timeout);
}

export function toastError(error) {
    toast(error instanceof ApiError || error instanceof Error ? error.message : String(error), { type: "error" });
}

// ---------- Dialogs ----------

function mountDialog(dialog) {
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
    return dialog;
}

/** await confirmDialog({ title, message, confirmText }) -> true/false */
export function confirmDialog({ title, message, confirmText = "Delete", cancelText = "Cancel", danger = true } = {}) {
    return new Promise((resolve) => {
        let result = false;
        const confirmButton = el("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, type: "button" }, confirmText);
        const cancelButton = el("button", { class: "btn btn-secondary", type: "button" }, cancelText);
        const dialog = el("dialog", { class: "modal modal-sm", "aria-labelledby": "confirm-title" },
            el("div", { class: "confirm-body" },
                el("div", { class: "confirm-icon" }, icon("alert", 20)),
                el("div", {}, el("h2", { id: "confirm-title" }, title), message ? el("p", {}, message) : null),
            ),
            el("div", { class: "modal-footer" }, cancelButton, confirmButton),
        );
        confirmButton.addEventListener("click", () => { result = true; dialog.close(); });
        cancelButton.addEventListener("click", () => dialog.close());
        dialog.addEventListener("close", () => resolve(result));
        mountDialog(dialog);
        cancelButton.focus(); // safe default for destructive actions
    });
}

// ---------- Forms ----------

export function setFormValues(form, values = {}) {
    for (const element of form.elements) {
        if (!element.name || !(element.name in values)) continue;
        const value = values[element.name];
        if (element.type === "checkbox") element.checked = Boolean(value);
        else if (element.type === "radio") element.checked = String(element.value) === String(value);
        else if (element.multiple) [...element.options].forEach((o) => { o.selected = (value || []).map(String).includes(o.value); });
        else if (element.dataset.type === "list") element.value = (value || []).join(", ");
        else element.value = value ?? "";
    }
}

/** Reads named fields. data-type="number" -> number|null, data-type="list" -> string[], checkbox -> boolean. */
export function getFormValues(form) {
    const values = {};
    for (const element of form.elements) {
        if (!element.name || element.disabled || element.type === "submit" || element.type === "button") continue;
        if (element.type === "checkbox") values[element.name] = element.checked;
        else if (element.type === "radio") { if (element.checked) values[element.name] = element.value; }
        else if (element.multiple) values[element.name] = [...element.selectedOptions].map((o) => o.value);
        else if (element.dataset.type === "number") values[element.name] = element.value === "" ? null : Number(element.value);
        else if (element.dataset.type === "list") values[element.name] = element.value.split(",").map((s) => s.trim()).filter(Boolean);
        else values[element.name] = element.value;
    }
    return values;
}

export function clearFieldErrors(form) {
    form.querySelectorAll(".field.has-error").forEach((field) => field.classList.remove("has-error"));
    form.querySelectorAll(".field .error, .form-error-summary").forEach((node) => node.remove());
    form.querySelectorAll("[aria-invalid]").forEach((node) => node.removeAttribute("aria-invalid"));
}

/** Show {field: message} from the API next to the matching inputs; unmatched ones go in a summary. */
export function showFieldErrors(form, fields = {}, summary = "") {
    clearFieldErrors(form);
    const unmatched = [];
    let first = null;
    for (const [name, message] of Object.entries(fields)) {
        const input = form.elements.namedItem(name.split(".")[0]);
        const field = input?.closest?.(".field") || (input && input.length && input[0].closest(".field"));
        if (!field) { unmatched.push(message); continue; }
        field.classList.add("has-error");
        field.append(el("p", { class: "error" }, message));
        (input.setAttribute ? input : input[0]).setAttribute("aria-invalid", "true");
        first ??= input.focus ? input : input[0];
    }
    if (summary || unmatched.length) {
        const container = form.querySelector(".modal-body") || form;
        container.prepend(el("div", { class: "form-error-summary", role: "alert" }, [summary, ...unmatched].filter(Boolean).join(" ")));
    }
    first?.focus();
}

/**
 * Open a modal containing a form cloned from a <template>.
 *   const saved = await openFormModal({
 *       title: "New event", template: "#event-form", values: {...},
 *       submitText: "Create", onSubmit: (data) => api(...),   // return value resolves the promise
 *   });
 * Resolves with onSubmit's result, or null if cancelled. Server field errors are shown inline.
 */
export function openFormModal({ title, description, template, values, submitText = "Save", size = "", onSubmit, onOpen }) {
    return new Promise((resolve) => {
        const source = typeof template === "string" ? document.querySelector(template) : template;
        if (!source) throw new Error(`Form template not found: ${template}`);

        let result = null;
        const submitButton = el("button", { class: "btn btn-primary", type: "submit" }, submitText);
        const cancelButton = el("button", { class: "btn btn-secondary", type: "button" }, "Cancel");
        const body = el("div", { class: "modal-body" });
        body.append(source.content.cloneNode(true));

        const form = el("form", { class: "modal-form", novalidate: true },
            el("div", { class: "modal-header" },
                el("div", {}, el("h2", { id: "modal-title" }, title), description ? el("p", {}, description) : null),
                el("button", { class: "icon-button", type: "button", "aria-label": "Close", onclick: () => dialog.close() }, icon("x")),
            ),
            body,
            el("div", { class: "modal-footer" }, cancelButton, submitButton),
        );
        const dialog = el("dialog", { class: `modal ${size ? "modal-" + size : ""}`, "aria-labelledby": "modal-title" }, form);

        if (values) setFormValues(form, values);
        cancelButton.addEventListener("click", () => dialog.close());
        dialog.addEventListener("close", () => resolve(result));

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            clearFieldErrors(form);
            submitButton.disabled = true;
            submitButton.classList.add("is-loading");
            try {
                result = (await onSubmit(getFormValues(form), form)) ?? true;
                dialog.close();
            } catch (error) {
                if (error instanceof ApiError && Object.keys(error.fields).length) {
                    showFieldErrors(form, error.fields, error.message);
                } else {
                    showFieldErrors(form, {}, error.message || "Something went wrong.");
                }
            } finally {
                submitButton.disabled = false;
                submitButton.classList.remove("is-loading");
            }
        });

        mountDialog(dialog);
        onOpen?.(form);
        form.querySelector("input:not([type=hidden]), select, textarea")?.focus();
    });
}
