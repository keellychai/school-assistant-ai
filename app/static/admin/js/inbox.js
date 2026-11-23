// The signed-in user's notification bell (top bar). Polls every 60 s; opening
// the inbox also lets the server deliver anything that has just come due.

import { api } from "./api.js";
import { el, relativeTime } from "./ui.js";

const root = document.querySelector("[data-inbox]");

if (root) {
    const count = root.querySelector(".inbox-count");
    const list = root.querySelector("[data-inbox-list]");
    const toggle = root.querySelector("[data-dropdown-toggle]");
    const menu = root.querySelector(".dropdown-menu");

    const setCount = (unread) => {
        count.hidden = !unread;
        count.textContent = unread > 9 ? "9+" : String(unread);
        toggle.setAttribute("aria-label", unread ? `Notifications, ${unread} unread` : "Notifications");
    };

    async function markRead(item, row) {
        if (item.read) return;
        try {
            const { unread } = await api(`/api/me/notifications/${item.id}/read`, { method: "POST" });
            item.read = true;
            row.classList.remove("is-unread");
            setCount(unread);
        } catch { /* not important enough to interrupt the user */ }
    }

    function render(items) {
        if (!items.length) {
            list.replaceChildren(el("p", { class: "inbox-empty" }, "You're all caught up."));
            return;
        }
        list.replaceChildren(...items.map((item) => {
            const body = el("div", { class: "inbox-body prose" });
            body.innerHTML = item.body_html; // sanitised on the server
            // Not a <button>: the message may contain links, which can't be nested inside a button.
            const row = el("div", { class: `dropdown-item inbox-item${item.read ? "" : " is-unread"}`, role: "menuitem", tabindex: "0" },
                el("span", { class: "inbox-dot", "aria-hidden": "true" }),
                el("span", { class: "inbox-text" },
                    el("strong", {}, item.title), body,
                    el("small", {}, relativeTime(item.delivered_at))));
            const open = (event) => {
                if (event.target.closest("a")) return; // let links work normally
                markRead(item, row);
                row.classList.toggle("is-expanded");
            };
            row.addEventListener("click", open);
            row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(event); } });
            return row;
        }));
    }

    async function load({ background = false } = {}) {
        try {
            const data = await api("/api/me/notifications", { params: { limit: 15 }, background });
            setCount(data.unread);
            render(data.items);
        } catch { /* keep the last state; try again on the next poll */ }
    }

    root.querySelector("[data-inbox-read-all]").addEventListener("click", async (event) => {
        event.stopPropagation();
        try {
            await api("/api/me/notifications/read-all", { method: "POST" });
            list.querySelectorAll(".is-unread").forEach((row) => row.classList.remove("is-unread"));
            setCount(0);
        } catch { /* ignore */ }
    });

    // This runs before the shared dropdown handler opens the menu, so "hidden" means "about to open".
    toggle.addEventListener("click", () => { if (menu.hidden) load(); });
    window.addEventListener("inbox:refresh", () => load());
    load({ background: true });
    setInterval(() => { if (!document.hidden) load({ background: true }); }, 60000);
}
