// Month calendar for events (Monday-first, 6-week grid).
// Dates are the school's LOCAL dates as sent by the API (start_date / end_date),
// so no timezone maths happens in the browser.
//
//   const cal = new MonthCalendar(el, {
//       month: "2026-12",                       // YYYY-MM
//       onEventClick: (event) => {},
//       onDayClick: (isoDate) => {},            // omit to disable creating from a day
//       onMoreClick: (isoDate) => {},
//       onMonthChange: (month) => {},
//   });
//   cal.reload();

import { api } from "./api.js";
import { el, icon, toastError } from "./ui.js";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MAX_CHIPS = 3;

const pad = (n) => String(n).padStart(2, "0");
const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
// Build dates at local noon so DST shifts can never move them to another day.
const makeDate = (y, m, d) => new Date(y, m, d, 12);

export function currentMonth(timeZone) {
    const today = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit" }).format(new Date());
    return today.slice(0, 7);
}

export class MonthCalendar {
    constructor(root, options) {
        this.root = root;
        this.options = options;
        this.month = options.month;
        this.events = [];
        this.today = null;
        this._build();
    }

    _build() {
        this.title = el("h2", { class: "calendar-title", "aria-live": "polite" });
        this.grid = el("div", { class: "calendar-grid", role: "grid" });
        this.root.replaceChildren(
            el("div", { class: "calendar-toolbar" },
                el("button", { class: "icon-button", type: "button", "aria-label": "Previous month", onclick: () => this.shift(-1) }, icon("chevron-left")),
                el("button", { class: "icon-button", type: "button", "aria-label": "Next month", onclick: () => this.shift(1) }, icon("chevron-right")),
                this.title,
                el("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => this.goToday() }, "Today"),
            ),
            el("div", { class: "calendar-weekdays", "aria-hidden": "true" }, ...WEEKDAYS.map((d) => el("span", {}, d))),
            this.grid,
        );
    }

    _range() {
        const [y, m] = this.month.split("-").map(Number);
        const first = makeDate(y, m - 1, 1);
        const offset = (first.getDay() + 6) % 7; // Monday = 0
        const start = makeDate(y, m - 1, 1 - offset);
        const days = Array.from({ length: 42 }, (_, i) => makeDate(start.getFullYear(), start.getMonth(), start.getDate() + i));
        return { y, m, days };
    }

    shift(delta) {
        const [y, m] = this.month.split("-").map(Number);
        const d = makeDate(y, m - 1 + delta, 1);
        this.setMonth(`${d.getFullYear()}-${pad(d.getMonth() + 1)}`);
    }

    goToday() {
        if (this.today) this.setMonth(this.today.slice(0, 7));
    }

    setMonth(month) {
        this.month = month;
        this.options.onMonthChange?.(month);
        this.reload();
    }

    async reload() {
        const { days } = this._range();
        this._render(); // draw the empty grid immediately
        this.grid.classList.add("is-loading");
        try {
            const data = await api("/api/admin/events/calendar", { params: { start: isoDate(days[0]), end: isoDate(days[41]) } });
            this.events = data.items;
            this.today = data.today;
            this._render();
        } catch (error) {
            toastError(error);
        } finally {
            this.grid.classList.remove("is-loading");
        }
    }

    _render() {
        const { y, m, days } = this._range();
        this.title.textContent = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(makeDate(y, m - 1, 1));

        this.grid.replaceChildren(...days.map((day) => {
            const date = isoDate(day);
            const inMonth = day.getMonth() === m - 1;
            const items = this.events.filter((e) => e.start_date <= date && date <= e.end_date);
            const label = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" }).format(day);

            const cell = el("div", {
                class: `calendar-day${inMonth ? "" : " is-outside"}${date === this.today ? " is-today" : ""}`,
                role: "gridcell", "aria-label": `${label}, ${items.length} event${items.length === 1 ? "" : "s"}`,
            });
            const header = el("div", { class: "calendar-day-header" }, el("span", { class: "calendar-date" }, String(day.getDate())));
            if (this.options.onDayClick) {
                header.append(el("button", {
                    class: "calendar-add", type: "button", "aria-label": `Add event on ${label}`,
                    onclick: () => this.options.onDayClick(date),
                }, icon("plus", 14)));
            }
            cell.append(header);

            items.slice(0, MAX_CHIPS).forEach((event) => {
                const continues = event.start_date < date;
                cell.append(el("button", {
                    class: `calendar-chip status-${event.status}${event.all_day ? " is-all-day" : ""}${continues ? " is-continued" : ""}`,
                    type: "button", title: `${event.title}${event.location ? " · " + event.location : ""}`,
                    onclick: () => this.options.onEventClick?.(event),
                },
                    !event.all_day && !continues && event.start_time ? el("span", { class: "chip-time" }, event.start_time) : null,
                    el("span", { class: "chip-title" }, event.title)));
            });
            if (items.length > MAX_CHIPS) {
                cell.append(el("button", { class: "calendar-more", type: "button", onclick: () => this.options.onMoreClick?.(date) },
                    `+${items.length - MAX_CHIPS} more`));
            }
            return cell;
        }));
    }
}
