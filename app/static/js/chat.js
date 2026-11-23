// AI Chat page. Everything shown comes from /api/chat/* (assistant settings,
// suggested questions, follow-ups, history). User text is always inserted as
// text; answer HTML comes from the server's safe Markdown renderer.

import { api } from "../admin/js/api.js";
import { el, relativeTime, formatDateTime } from "../admin/js/ui.js";

const $ = (id) => document.getElementById(id);
const messages = $("chat-messages");
const form = $("chat-form");
const input = $("question-input");
const sendButton = $("send-button");
const historyPanel = $("chat-history");
const historyToggle = $("history-toggle");

// Small notes shown under answers follow the language chosen in System settings.
const TEXT = document.documentElement.lang === "id"
    ? { review: "Informasi ini belum ada di data sekolah. Pertanyaan Anda sudah diteruskan agar bisa dilengkapi." }
    : { review: "I couldn't find this in the school's information yet. Your question has been passed on so it can be added." };

let config = null;
let sessionId = null;
let sending = false;

// ---------- rendering ----------

function avatar(node) {
    node.replaceChildren();
    if (config.assistant.avatar_url) node.append(el("img", { src: config.assistant.avatar_url, alt: "" }));
    else node.textContent = (config.assistant.name || "A").trim().slice(0, 1).toUpperCase();
}

function scrollToEnd() {
    messages.scrollTop = messages.scrollHeight;
}

function chips(items, label) {
    if (!items.length) return null;
    return el("div", { class: "suggestion-area", role: "group", "aria-label": label },
        el("span", {}, label.toUpperCase()),
        el("div", { class: "suggestion-list" }, ...items.map((q) =>
            el("button", { type: "button", class: "suggestion-chip", onclick: () => send(q.text, q.id) }, q.text))));
}

function botRow(...content) {
    const icon = el("div", { class: "message-avatar", "aria-hidden": "true" });
    avatar(icon);
    return el("div", { class: "message-row assistant-row" }, icon, el("div", { class: "message-bubble" }, ...content));
}

function renderWelcome() {
    const greeting = config.assistant.initial_greeting;
    messages.replaceChildren(botRow(
        el("p", {}, config.assistant.welcome_message),
        greeting ? el("p", {}, greeting) : null,
        el("span", { class: "message-time" }, config.assistant.name),
    ));
    const suggested = chips(config.suggested, "Try asking");
    if (suggested) messages.append(suggested);
    messages.setAttribute("aria-busy", "false");
}

function userRow(message) {
    return el("div", { class: "message-row user-row" }, el("div", { class: "message-bubble" },
        el("p", {}, message.text),
        el("span", { class: "message-time", title: message.created_at ? formatDateTime(message.created_at) : "" }, "You")));
}

function replyRow(message) {
    const body = el("div", { class: "answer-content" });
    body.innerHTML = message.html;  // server-rendered, sanitised Markdown
    body.querySelectorAll("a[href^='http']").forEach((a) => { a.target = "_blank"; a.rel = "noopener noreferrer"; });

    const links = message.links.length ? el("ul", { class: "answer-links" }, ...message.links.map((link) =>
        el("li", {}, el("a", { href: link.url, ...(link.external ? { target: "_blank", rel: "noopener noreferrer" } : {}) },
            link.label, link.external ? " ↗" : "")))) : null;
    const source = message.source ? el("div", { class: "source-card" },
        el("span", {}, message.source.type === "ai" ? "AI answer" : "Source"), el("strong", {}, message.source.title)) : null;
    const review = message.resolved_by === "fallback" ? el("div", { class: "review-note" }, TEXT.review) : null;
    const time = el("span", { class: "message-time", title: formatDateTime(message.created_at) }, config.assistant.name);
    const row = botRow(body, links, source, review, time);
    row.dataset.messageId = message.id;
    return row;
}

function showFollowUps(message) {
    messages.querySelectorAll(".follow-ups").forEach((n) => n.remove());
    const group = chips(message.follow_ups || [], "Related questions");
    if (group) { group.classList.add("follow-ups"); messages.append(group); }
}

function typingRow() {
    return botRow(el("div", { class: "typing", "aria-label": "Assistant is typing" }, el("i"), el("i"), el("i")));
}

// ---------- history ----------

function setUrl() {
    const url = new URL(window.location.href);
    url.searchParams.delete("ask");
    url.searchParams.delete("q");
    if (sessionId && config.features.chat_history) url.searchParams.set("c", sessionId);
    else url.searchParams.delete("c");
    history.replaceState(null, "", url.pathname + url.search);
}

async function loadHistory() {
    if (!config.features.chat_history) return;
    try {
        const data = await api("/api/chat/sessions");
        const list = $("history-list");
        list.replaceChildren(...(data.items.length ? data.items.map((s) => el("li", {},
            el("button", { type: "button", class: `history-item${s.id === sessionId ? " is-active" : ""}`,
                "aria-current": s.id === sessionId ? "true" : null, onclick: () => openSession(s.id) },
                el("strong", {}, s.title), el("small", {}, relativeTime(s.last_message_at))))) :
            [el("li", { class: "history-empty" }, "Your conversations will appear here.")]));
    } catch { /* the chat itself still works */ }
}

async function openSession(id) {
    if (sending) return;
    closeHistoryOnMobile();
    messages.setAttribute("aria-busy", "true");
    try {
        const data = await api(`/api/chat/sessions/${encodeURIComponent(id)}`);
        sessionId = data.id;
        messages.replaceChildren(...data.messages.map((m) => (m.role === "user" ? userRow(m) : replyRow(m))));
        const last = data.messages.at(-1);
        if (last?.role === "assistant") showFollowUps(last);
        setUrl();
        scrollToEnd();
        loadHistory();
    } catch (error) {
        sessionId = null;
        setUrl();
        renderWelcome();
        notice(error.status === 404 ? "That conversation isn't available." : error.message);
    } finally {
        messages.setAttribute("aria-busy", "false");
    }
}

function newConversation() {
    if (sending) return;
    sessionId = null;
    setUrl();
    renderWelcome();
    loadHistory();
    closeHistoryOnMobile();
    input.focus();
}

function closeHistoryOnMobile() {
    if (window.matchMedia("(max-width: 900px)").matches) {
        historyPanel.classList.remove("is-open");
        historyToggle.setAttribute("aria-expanded", "false");
    }
}

// ---------- sending ----------

function notice(text) {
    messages.append(el("div", { class: "chat-notice", role: "alert" }, text));
    scrollToEnd();
}

async function send(text, questionId = null) {
    text = (text || "").trim();
    if (!text || sending) return;
    sending = true;
    sendButton.disabled = true;
    messages.querySelectorAll(".suggestion-area").forEach((n) => n.remove());
    messages.querySelectorAll(".chat-notice").forEach((n) => n.remove());

    const pending = userRow({ text });
    const typing = typingRow();
    messages.append(pending, typing);
    input.value = "";
    autosize();
    scrollToEnd();

    try {
        const data = await api("/api/chat/messages", {
            method: "POST", body: { message: text, session_id: sessionId, question_id: questionId },
        });
        sessionId = data.session_id;
        typing.replaceWith(replyRow(data.reply));
        showFollowUps(data.reply);
        setUrl();
        loadHistory();
    } catch (error) {
        typing.remove();
        pending.remove();
        input.value = text;
        autosize();
        notice(error.status === 429 ? "You're sending messages too quickly. Wait a moment and try again."
            : (error.fields?.message || error.message || "Something went wrong. Please try again."));
    } finally {
        sending = false;
        sendButton.disabled = false;
        scrollToEnd();
        input.focus();
    }
}

// ---------- input ----------

function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
    const left = (config?.max_length || 500) - input.value.length;
    $("char-count").textContent = left <= 100 ? `${left} characters left · ` : "";
}

input.addEventListener("input", autosize);
input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        form.requestSubmit();
    }
});
form.addEventListener("submit", (event) => { event.preventDefault(); send(input.value); });
document.querySelectorAll("[data-new-chat]").forEach((b) => b.addEventListener("click", newConversation));
historyToggle.addEventListener("click", () => {
    const open = historyPanel.classList.toggle("is-open");
    historyToggle.setAttribute("aria-expanded", String(open));
});

// ---------- start ----------

async function init() {
    try {
        config = await api("/api/chat/config");
    } catch (error) {
        messages.replaceChildren(el("div", { class: "chat-notice", role: "alert" }, "AI Chat couldn't load. Refresh the page to try again."));
        return;
    }
    avatar($("bot-avatar"));
    $("bot-name").textContent = config.assistant.name;
    input.maxLength = config.max_length;
    if (config.features.chat_history) {
        historyPanel.hidden = false;
        historyToggle.hidden = false;
        document.getElementById("chat-layout").classList.add("has-history");
    }

    const params = new URLSearchParams(window.location.search);
    if (params.get("c") && config.features.chat_history) {
        await openSession(params.get("c"));
    } else {
        renderWelcome();
        loadHistory();
        // Arriving from a suggested question on the dashboard
        const ask = params.get("ask");
        if (params.get("q")) send(params.get("q"), ask && /^\d+$/.test(ask) ? Number(ask) : null);
    }
    autosize();
    input.focus();
}

init();
