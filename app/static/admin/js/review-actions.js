// Actions on unanswered AI Chat questions, shared by the Conversations pages:
//   const actions = reviewActions({ canReview, canAddQuestion, onChange });
//   actions.setReviewed([id], true); actions.addAsQuestion({ id, question });

import { api, ApiError } from "./api.js";
import { el, openFormModal, toast, toastError } from "./ui.js";
import { fillSelect } from "./shared.js";

const FORM_ID = "review-question-form";

function ensureTemplate() {
    if (document.getElementById(FORM_ID)) return;
    const template = el("template", { id: FORM_ID });
    template.innerHTML = `
        <div class="field">
            <label class="required" for="rq-text">Question</label>
            <input class="input" id="rq-text" name="text" maxlength="500">
            <p class="hint">Tidy the wording if you like: this is how it's matched and shown in AI Chat.</p>
        </div>
        <div class="field">
            <label class="required" for="rq-category">Category</label>
            <select class="select" id="rq-category" name="category_id" data-type="number"></select>
        </div>
        <div class="field">
            <label for="rq-keywords">Keywords</label>
            <input class="input" id="rq-keywords" name="keywords" data-type="list" placeholder="spp, biaya sekolah">
            <p class="hint">Comma-separated words or phrases people might use for this question.</p>
        </div>
        <div class="field">
            <label class="required" for="rq-answer">Answer</label>
            <textarea class="textarea" id="rq-answer" name="answer" placeholder="You can use **bold**, lists and [links](https://example.com)."></textarea>
        </div>`;
    document.body.append(template);
}

export function reviewActions({ canReview, canAddQuestion, questionUrl, onChange }) {
    let categories = null;

    async function setReviewed(ids, reviewed) {
        try {
            await api("/api/admin/conversations/review", { method: "POST", body: { ids, reviewed } });
            toast(reviewed ? "Marked as reviewed." : "Moved back to Needs review.");
            onChange?.();
        } catch (error) { toastError(error); }
    }

    async function addAsQuestion(item) {
        ensureTemplate();
        try {
            categories ??= await api("/api/admin/chat/categories/options");
        } catch (error) { return toastError(error); }
        if (!categories.length) return toastError(new Error("Create a question category first (AI Assistant → Categories)."));

        const created = await openFormModal({
            title: "Answer this question",
            description: "Creates a configured question so AI Chat answers it from now on.",
            template: `#${FORM_ID}`, size: "lg", submitText: "Create question",
            values: { text: item.question },
            onOpen: (form) => fillSelect(form.elements.category_id, categories, { placeholder: "Choose a category…", label: "name" }),
            onSubmit: (data) => {
                if (!(data.answer || "").trim()) {
                    return Promise.reject(new ApiError("Please correct the highlighted fields.", 422,
                        { answer: "Write the answer users should get." }));
                }
                return api("/api/admin/chat/questions", { method: "POST",
                    body: { ...data, is_enabled: true, is_suggested: false, display_order: 0 } });
            },
        });
        if (!created || created === true) return;
        if (canReview && !item.reviewed_at) {
            try { await api("/api/admin/conversations/review", { method: "POST", body: { ids: [item.id], reviewed: true } }); }
            catch { /* the question was still created */ }
        }
        toast("Question created. AI Chat will answer it from now on.");
        onChange?.(created);
        return created;
    }

    function rowActions(item) {
        return [
            canAddQuestion ? { label: "Answer this question…", onClick: () => addAsQuestion(item) } : null,
            canReview ? (item.reviewed_at
                ? { label: "Move back to Needs review", onClick: () => setReviewed([item.id], false) }
                : { label: "Mark as reviewed", onClick: () => setReviewed([item.id], true) }) : null,
            item.conversation_id ? { label: "Open conversation", onClick: () => { window.location.href = `/admin/conversations/${item.conversation_id}`; } } : null,
        ];
    }

    return { setReviewed, addAsQuestion, rowActions, questionUrl };
}
