// Small building blocks reused across admin module pages.

import { el, icon, toastError } from "./ui.js";

/** Replace a <select>'s options (keeps an optional placeholder first). */
export function fillSelect(select, items, { value = "id", label = "name", placeholder, selected } = {}) {
    const current = selected ?? select.value;
    select.replaceChildren();
    if (placeholder !== undefined) select.append(el("option", { value: "" }, placeholder));
    for (const item of items) {
        const text = typeof label === "function" ? label(item) : item[label];
        select.append(el("option", { value: item[value] }, text));
    }
    select.value = current ?? "";
}

/**
 * Toggle switch that saves immediately. If onChange rejects, the switch flips
 * back and the error is shown, so the UI never claims a state that wasn't saved.
 */
export function switchControl({ checked, label, disabled = false, onChange }) {
    const input = el("input", { type: "checkbox", disabled, "aria-label": label });
    input.checked = Boolean(checked);
    input.addEventListener("change", async () => {
        const value = input.checked;
        input.disabled = true;
        try {
            await onChange(value);
        } catch (error) {
            input.checked = !value;
            toastError(error);
        } finally {
            input.disabled = disabled;
        }
    });
    return el("label", { class: "switch", title: label }, input, el("span", { class: "switch-track" }),
        el("span", { class: "sr-only" }, label));
}

export function badge(text, variant = "") {
    return el("span", { class: `badge ${variant ? "badge-" + variant : ""}` }, text);
}

export function move(array, index, delta) {
    const target = index + delta;
    if (target < 0 || target >= array.length) return array;
    const copy = [...array];
    [copy[index], copy[target]] = [copy[target], copy[index]];
    return copy;
}

/** Up / down / remove buttons for a row in an ordered list. */
export function orderButtons({ index, length, onMove, onRemove, removeLabel = "Remove" }) {
    return el("div", { class: "order-actions" },
        el("button", { class: "icon-button", type: "button", "aria-label": "Move up", disabled: index === 0, onclick: () => onMove(-1) }, icon("arrow-up", 16)),
        el("button", { class: "icon-button", type: "button", "aria-label": "Move down", disabled: index === length - 1, onclick: () => onMove(1) }, icon("arrow-down", 16)),
        onRemove ? el("button", { class: "icon-button", type: "button", "aria-label": removeLabel, onclick: onRemove }, icon("trash", 16)) : null,
    );
}

/** Show a generated password once, with a copy button. Resolves when closed. */
export function revealPassword({ email, password, title = "Temporary password" }) {
    return new Promise((resolve) => {
        const code = el("code", { class: "secret-value" }, password);
        const copy = el("button", { class: "btn btn-secondary btn-sm", type: "button" }, "Copy");
        copy.addEventListener("click", async () => {
            try { await navigator.clipboard.writeText(password); copy.textContent = "Copied"; }
            catch { copy.textContent = "Select and copy it manually"; }
        });
        const done = el("button", { class: "btn btn-primary", type: "button" }, "I've saved it");
        const dialog = el("dialog", { class: "modal modal-sm", "aria-labelledby": "secret-title" },
            el("div", { class: "modal-header" }, el("div", {}, el("h2", { id: "secret-title" }, title),
                el("p", {}, `For ${email}. Give it to them securely.`))),
            el("div", { class: "modal-body" },
                el("div", { class: "secret-box" }, code, copy),
                el("p", { class: "alert alert-info", style: "margin: 14px 0 0" },
                    "It won't be shown again. Ask them to change it after signing in.")),
            el("div", { class: "modal-footer" }, done));
        done.addEventListener("click", () => dialog.close());
        dialog.addEventListener("close", () => { dialog.remove(); resolve(); });
        document.body.append(dialog);
        dialog.showModal();
        copy.focus();
    });
}

/** Two-letter initials for an avatar. */
export function initials(name) {
    const parts = (name || "?").trim().split(/\s+/);
    return ((parts[0]?.[0] || "") + (parts.length > 1 ? parts.at(-1)[0] : parts[0]?.[1] || "")).toUpperCase();
}

/** Warn before leaving the page with unsaved changes. Returns { set(dirty), get() }. */
export function unsavedChangesGuard() {
    let dirty = false;
    window.addEventListener("beforeunload", (event) => {
        if (dirty) { event.preventDefault(); event.returnValue = ""; }
    });
    return { set: (value) => { dirty = value; }, get: () => dirty };
}
