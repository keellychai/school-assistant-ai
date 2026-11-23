// Layout behaviour shared by every admin page: mobile sidebar, dropdowns, theme toggle.

const body = document.body;
const root = document.documentElement;

// ----- Sidebar drawer (below 1024px) -----

function setSidebar(open) {
    body.classList.toggle("sidebar-open", open);
    document.querySelector("[data-sidebar-open]")?.setAttribute("aria-expanded", String(open));
    if (open) document.querySelector("#sidebar .nav-item")?.focus();
}

document.addEventListener("click", (event) => {
    if (event.target.closest("[data-sidebar-open]")) setSidebar(true);
    else if (event.target.closest("[data-sidebar-close]")) setSidebar(false);
});

// ----- Dropdowns (event delegation, so dynamically added ones work too) -----

function closeDropdowns(except) {
    document.querySelectorAll("[data-dropdown]").forEach((dropdown) => {
        if (dropdown === except) return;
        const menu = dropdown.querySelector(".dropdown-menu");
        if (menu && !menu.hidden) {
            menu.hidden = true;
            dropdown.querySelector("[data-dropdown-toggle]")?.setAttribute("aria-expanded", "false");
        }
    });
}

document.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-dropdown-toggle]");
    const dropdown = event.target.closest("[data-dropdown]");
    if (toggle && dropdown) {
        const menu = dropdown.querySelector(".dropdown-menu");
        const open = menu.hidden;
        closeDropdowns(dropdown);
        menu.hidden = !open;
        toggle.setAttribute("aria-expanded", String(open));
        if (open) {
            if (dropdown.closest(".table-wrap")) floatMenu(menu, toggle);
            menu.querySelector(".dropdown-item")?.focus();
        }
        return;
    }
    if (!dropdown) closeDropdowns();
});

// Menus inside scrollable tables would be clipped, so pin them to the viewport
// (opening upwards when there isn't room below).
function floatMenu(menu, toggle) {
    const rect = toggle.getBoundingClientRect();
    Object.assign(menu.style, { position: "fixed", left: "auto", right: `${window.innerWidth - rect.right}px`, top: `${rect.bottom + 6}px` });
    const height = menu.offsetHeight;
    if (rect.bottom + 6 + height > window.innerHeight && rect.top - 6 - height > 0) {
        menu.style.top = `${rect.top - 6 - height}px`;
    }
}

window.addEventListener("scroll", () => closeDropdowns(), { capture: true, passive: true });
window.addEventListener("resize", () => closeDropdowns());

document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const openMenu = document.querySelector("[data-dropdown] .dropdown-menu:not([hidden])");
    if (openMenu) {
        closeDropdowns();
        openMenu.closest("[data-dropdown]").querySelector("[data-dropdown-toggle]")?.focus();
    } else if (body.classList.contains("sidebar-open")) {
        setSidebar(false);
        document.querySelector("[data-sidebar-open]")?.focus();
    }
});

// ----- Theme toggle (remembered per browser) -----

document.querySelector("[data-theme-toggle]")?.addEventListener("click", () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("admin-theme", next); } catch {}
});
