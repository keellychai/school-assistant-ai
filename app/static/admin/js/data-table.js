// Reusable server-side table: search, filters, pagination, row action menu,
// loading/empty/error states. Table state is kept in the URL, so refreshing
// the page (or sharing the link) keeps the same search, filters and page.
//
//   const table = new DataTable(document.querySelector("#events-table"), {
//       endpoint: "/api/admin/events",
//       searchPlaceholder: "Search events…",
//       filters: [{ name: "status", label: "All statuses", options: [{ value: "upcoming", label: "Upcoming" }] }],
//       columns: [
//           { key: "title", label: "Title", className: "cell-primary" },
//           { key: "start_at", label: "Starts", render: (row) => formatDateTime(row.start_at) },
//       ],
//       rowActions: (row) => [{ label: "Edit", onClick: () => edit(row) }, { label: "Delete", danger: true, onClick: ... }],
//       empty: { title: "No events yet", text: "Create your first event." },
//   });
//   table.reload();   // after create/update/delete

import { api } from "./api.js";
import { debounce, el, icon } from "./ui.js";

export class DataTable {
    constructor(root, options) {
        this.root = root;
        this.options = { perPage: 20, searchPlaceholder: "Search…", filters: [], rowActions: null, ...options };
        this.state = this._readUrlState();
        this.controller = null;
        this._build();
        this.load();
    }

    // ----- state <-> URL -----

    _filterNames() {
        return this.options.filters.map((f) => f.name);
    }

    _readUrlState() {
        const params = new URLSearchParams(window.location.search);
        const state = { q: params.get("q") || "", page: Number(params.get("page")) || 1, filters: {} };
        for (const filter of this.options.filters) state.filters[filter.name] = params.get(filter.name) || "";
        return state;
    }

    _writeUrlState() {
        const params = new URLSearchParams(window.location.search);
        const set = (key, value) => (value ? params.set(key, value) : params.delete(key));
        set("q", this.state.q);
        set("page", this.state.page > 1 ? this.state.page : "");
        for (const name of this._filterNames()) set(name, this.state.filters[name]);
        const query = params.toString();
        history.replaceState(null, "", window.location.pathname + (query ? "?" + query : ""));
    }

    // ----- building -----

    _build() {
        const search = el("input", {
            class: "input", type: "search", placeholder: this.options.searchPlaceholder,
            "aria-label": this.options.searchPlaceholder, value: this.state.q,
        });
        search.addEventListener("input", debounce(() => {
            this.state.q = search.value.trim();
            this.state.page = 1;
            this.load();
        }, 300));

        this.filterSelects = this.options.filters.map((filter) => {
            const select = el("select", { class: "select", "aria-label": filter.label, name: filter.name },
                el("option", { value: "" }, filter.label));
            this._setOptions(select, filter.options || []);
            select.value = this.state.filters[filter.name] || "";
            select.addEventListener("change", () => {
                this.state.filters[filter.name] = select.value;
                this.state.page = 1;
                this.load();
            });
            return select;
        });

        this.toolbar = el("div", { class: "toolbar" },
            el("label", { class: "input-icon" }, icon("search", 16), search),
            ...this.filterSelects,
            el("span", { class: "toolbar-spacer" }),
            this.options.toolbarExtra || null,
        );

        this.thead = el("thead", {}, el("tr", {},
            ...this.options.columns.map((c) => el("th", { scope: "col", class: c.headerClass }, c.label)),
            this.options.rowActions ? el("th", { scope: "col", class: "cell-actions" }, el("span", { class: "sr-only" }, "Actions")) : null,
        ));
        this.tbody = el("tbody");
        this.table = el("table", { class: "table responsive" }, this.thead, this.tbody);
        this.stateBox = el("div", { hidden: true });
        this.footer = el("div", { class: "table-footer" });

        this.root.replaceChildren(this.toolbar, el("div", { class: "table-wrap" }, this.table), this.stateBox, this.footer);
    }

    _setOptions(select, options) {
        const current = select.value;
        select.querySelectorAll("option:not(:first-child)").forEach((o) => o.remove());
        for (const option of options) {
            const { value, label } = typeof option === "object" ? option : { value: option, label: option };
            select.append(el("option", { value }, label));
        }
        select.value = current;
    }

    /** Replace a filter's options later (e.g. after loading them from the API). */
    setFilterOptions(name, options) {
        const index = this._filterNames().indexOf(name);
        if (index === -1) return;
        this._setOptions(this.filterSelects[index], options);
        this.filterSelects[index].value = this.state.filters[name] || "";
    }

    // ----- loading -----

    reload() {
        return this.load();
    }

    async load() {
        this._writeUrlState();
        this.controller?.abort();
        this.controller = new AbortController();
        this._renderLoading();
        try {
            const data = await api(this.options.endpoint, {
                params: {
                    q: this.state.q, page: this.state.page, per_page: this.options.perPage, ...this.state.filters,
                    ...(this.options.extraParams?.() || {}),  // page-managed params, e.g. a date range
                },
                signal: this.controller.signal,
            });
            this.data = data;
            this.state.page = data.page;
            this._writeUrlState();
            this._render(data);
        } catch (error) {
            if (error.name === "AbortError") return;
            this._renderState("error", "Couldn't load data", error.message, "Retry", () => this.load());
        }
    }

    // ----- rendering -----

    _renderLoading() {
        if (this.data?.items.length && !this.table.hidden) {
            this.table.style.opacity = "0.55"; // keep current rows visible (dimmed) while refreshing
            return;
        }
        this.stateBox.hidden = true;
        this.table.hidden = false;
        const cols = this.options.columns.length + (this.options.rowActions ? 1 : 0);
        this.tbody.replaceChildren(...Array.from({ length: 5 }, () =>
            el("tr", {}, ...Array.from({ length: cols }, (_, i) =>
                el("td", {}, el("span", { class: "skeleton", style: `width:${i === 0 ? 70 : 45}%` }))))));
    }

    _renderState(kind, title, text, actionLabel, action) {
        this.table.hidden = true;
        this.table.style.opacity = "";
        this.footer.hidden = true;
        this.stateBox.hidden = false;
        this.stateBox.className = `table-state${kind === "error" ? " is-error" : ""}`;
        // (replaceChildren would turn null into the text "null", so filter first)
        this.stateBox.replaceChildren(...[
            el("div", { class: "state-icon" }, icon(kind === "error" ? "alert" : "inbox", 20)),
            el("strong", {}, title),
            text ? el("p", {}, text) : null,
            actionLabel ? el("button", { class: "btn btn-secondary btn-sm", type: "button", style: "margin-top:14px", onclick: action }, actionLabel) : null,
        ].filter(Boolean));
    }

    _render(data) {
        this.table.style.opacity = "";
        if (!data.items.length) {
            const filtered = this.state.q || Object.values(this.state.filters).some(Boolean)
                || Object.values(this.options.extraParams?.() || {}).some(Boolean);
            const empty = this.options.empty || {};
            if (filtered) {
                this._renderState("empty", "No matching results", "Try a different search or clear the filters.", "Clear filters", () => this.clearFilters());
            } else {
                this._renderState("empty", empty.title || "Nothing here yet", empty.text || "", empty.actionLabel, empty.onAction);
            }
            return;
        }

        this.stateBox.hidden = true;
        this.table.hidden = false;
        this.footer.hidden = false;
        this.tbody.replaceChildren(...data.items.map((row) => this._renderRow(row)));
        this._renderFooter(data);
    }

    _renderRow(row) {
        const cells = this.options.columns.map((column) => {
            const value = column.render ? column.render(row) : row[column.key];
            const td = el("td", { class: column.className, "data-label": column.label });
            td.append(value instanceof Node ? value : document.createTextNode(value ?? "—"));
            return td;
        });
        if (this.options.rowActions) {
            const actions = this.options.rowActions(row).filter(Boolean);
            cells.push(el("td", { class: "cell-actions" }, actions.length ? this._actionMenu(actions) : null));
        }
        return el("tr", {}, ...cells);
    }

    _actionMenu(actions) {
        const menu = el("div", { class: "dropdown-menu", role: "menu", hidden: true },
            ...actions.map((action) => el("button", {
                class: `dropdown-item${action.danger ? " danger" : ""}`, type: "button", role: "menuitem",
                onclick: () => { menu.hidden = true; toggle.setAttribute("aria-expanded", "false"); action.onClick(); },
            }, action.label)));
        const toggle = el("button", {
            class: "icon-button", type: "button", "aria-label": "Row actions",
            "aria-haspopup": "menu", "aria-expanded": "false", "data-dropdown-toggle": true,
        }, icon("more"));
        return el("div", { class: "dropdown", "data-dropdown": true }, toggle, menu);
    }

    _renderFooter({ page, pages, total, per_page }) {
        const from = (page - 1) * per_page + 1;
        const to = Math.min(page * per_page, total);
        const go = (target) => { this.state.page = target; this.load(); };
        this.footer.replaceChildren(
            el("span", {}, `Showing ${from}–${to} of ${total}`),
            el("div", { class: "pagination" },
                el("button", { class: "icon-button", type: "button", "aria-label": "Previous page", disabled: page <= 1, onclick: () => go(page - 1) }, icon("chevron-left")),
                el("span", { class: "page-info" }, `Page ${page} of ${pages}`),
                el("button", { class: "icon-button", type: "button", "aria-label": "Next page", disabled: page >= pages, onclick: () => go(page + 1) }, icon("chevron-right")),
            ),
        );
    }

    clearFilters() {
        this.state.q = "";
        this.state.page = 1;
        for (const name of this._filterNames()) this.state.filters[name] = "";
        this.toolbar.querySelector("input[type=search]").value = "";
        this.filterSelects.forEach((s) => { s.value = ""; });
        this.load();
    }
}
