// Account actions shared by the Users list and the user profile page:
// reset password, unlock, activate / deactivate.

import { api } from "./api.js";
import { confirmDialog, openFormModal, toast, toastError } from "./ui.js";
import { revealPassword } from "./shared.js";

export function userActions({ onChange }) {
    async function resetPassword(user) {
        const result = await openFormModal({
            title: `Reset password for ${user.full_name}`, template: "#password-form", submitText: "Reset password", size: "sm",
            onOpen: (form) => {
                const input = form.elements.password;
                form.querySelectorAll('input[name="password_mode"]').forEach((radio) => radio.addEventListener("change", () => {
                    input.hidden = form.querySelector('input[name="password_mode"]:checked').value !== "manual";
                    if (!input.hidden) input.focus();
                }));
            },
            onSubmit: (data) => api(`/api/admin/users/${user.id}/reset-password`, {
                method: "POST", body: { password: data.password_mode === "manual" ? data.password : null } }),
        });
        if (!result || result === true) return;
        if (result.temporary_password) await revealPassword({ email: result.email, password: result.temporary_password, title: "New temporary password" });
        toast(user.is_you ? "Password changed. You'll need to sign in again." : "Password reset. They have been signed out everywhere.");
        if (user.is_you) { location.reload(); return; }
        onChange?.(result);
    }

    async function setActive(user, active) {
        if (!active && !await confirmDialog({
            title: `Deactivate ${user.full_name}?`,
            message: "They're signed out immediately and can't sign in until reactivated. Their history is kept.",
            confirmText: "Deactivate",
        })) return;
        try {
            const result = await api(`/api/admin/users/${user.id}/status`, { method: "PATCH", body: { is_active: active } });
            toast(active ? "Account activated." : "Account deactivated.");
            onChange?.(result);
        } catch (error) { toastError(error); }
    }

    async function unlock(user) {
        try {
            const result = await api(`/api/admin/users/${user.id}/unlock`, { method: "POST" });
            toast("Account unlocked.");
            onChange?.(result);
        } catch (error) { toastError(error); }
    }

    return {
        resetPassword, setActive, unlock,
        /** Row-menu entries for a user the current admin may manage. */
        menu(user, { edit } = {}) {
            return [
                edit && { label: "Edit", onClick: edit },
                { label: "Reset password", onClick: () => resetPassword(user) },
                user.is_locked && { label: "Unlock", onClick: () => unlock(user) },
                !user.is_you && (user.is_active
                    ? { label: "Deactivate", danger: true, onClick: () => setActive(user, false) }
                    : { label: "Activate", onClick: () => setActive(user, true) }),
            ].filter(Boolean);
        },
    };
}
