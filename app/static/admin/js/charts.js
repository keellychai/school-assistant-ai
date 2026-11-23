// Small dependency-free SVG charts for the dashboard.
// Marks use the accent colour; all text uses text tokens. Labels are inserted as text only.

import { el } from "./ui.js";

const SVG = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
};

// Whole-number ticks (counts): a 1/2/5 x 10^n step, about four gridlines.
function niceScale(value) {
    const raw = Math.max(value, 4) / 4;
    const magnitude = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= raw && Number.isInteger(s)) || Math.ceil(raw);
    return { step, max: Math.ceil(Math.max(value, 1) / step) * step };
}

// Column with a 4px rounded top and a square base.
function columnPath(x, y, w, h) {
    const r = Math.min(4, w / 2, h);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/**
 * Column chart for one series over time, with hover/focus tooltip and keyboard navigation.
 *   columnChart(container, { points: [{ label, short, value, extra }], valueLabel: "Conversations" })
 * Re-renders on resize. Returns { update(points) }.
 */
export function columnChart(container, { points, valueLabel, height = 220 }) {
    const tooltip = el("div", { class: "chart-tooltip", role: "status", hidden: true });
    const frame = el("div", { class: "chart-frame" });
    container.replaceChildren(frame, tooltip);
    let data = points;
    let active = -1;
    let bars = [];

    function showTip(index) {
        const point = data[index];
        if (!point) return;
        active = index;
        bars.forEach((b, i) => b.classList.toggle("is-active", i === index));
        tooltip.replaceChildren(
            el("strong", {}, point.value.toLocaleString()),
            el("span", {}, ` ${valueLabel.toLowerCase()}`),
            point.extra ? el("small", {}, point.extra) : null,
            el("small", {}, point.label),
        );
        tooltip.hidden = false;
        const bar = bars[index].getBoundingClientRect();
        const box = container.getBoundingClientRect();
        const left = Math.min(Math.max(bar.left - box.left + bar.width / 2, 70), box.width - 70);
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${Math.max(bar.top - box.top - 8, 0)}px`;
    }

    function hideTip() {
        active = -1;
        tooltip.hidden = true;
        bars.forEach((b) => b.classList.remove("is-active"));
    }

    function render() {
        const width = Math.max(frame.clientWidth, 260);
        const pad = { top: 12, right: 4, bottom: 24, left: 34 };
        const plotW = width - pad.left - pad.right;
        const plotH = height - pad.top - pad.bottom;
        const { step, max } = niceScale(Math.max(...data.map((p) => p.value), 0));
        const slot = plotW / data.length;
        const barW = Math.max(Math.min(24, slot - 2), 1);   // capped thickness, >= 2px surface gap
        const y = (v) => pad.top + plotH - (v / max) * plotH;

        const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}`, class: "chart-svg",
            role: "img", tabindex: "0", "aria-label": `${valueLabel} per day. Use the arrow keys to read each day.` });

        for (let value = 0; value <= max; value += step) {
            const i = value;
            const gy = y(value);
            svg.append(svgEl("line", { x1: pad.left, x2: width - pad.right, y1: gy, y2: gy, class: i === 0 ? "chart-axis" : "chart-grid" }));
            const label = svgEl("text", { x: pad.left - 8, y: gy + 4, class: "chart-tick", "text-anchor": "end" });
            label.textContent = value.toLocaleString();
            svg.append(label);
        }

        // x labels: about six, evenly spaced, always including the last day
        const every = Math.max(1, Math.ceil(data.length / 6));
        bars = data.map((point, i) => {
            const x = pad.left + slot * i + (slot - barW) / 2;
            const h = Math.max(plotH - (y(point.value) - pad.top), 0);
            const group = svgEl("g", { class: "chart-bar" });
            group.append(svgEl("rect", { x: pad.left + slot * i, y: pad.top, width: slot, height: plotH, class: "chart-hit" }));
            if (h > 0) group.append(svgEl("path", { d: columnPath(x, pad.top + plotH - h, barW, h), class: "chart-mark" }));
            group.addEventListener("pointerenter", () => showTip(i));
            group.addEventListener("pointerleave", hideTip);
            svg.append(group);
            if ((data.length - 1 - i) % every === 0) {
                const label = svgEl("text", { x: pad.left + slot * i + slot / 2, y: height - 6, class: "chart-tick", "text-anchor": "middle" });
                label.textContent = point.short;
                svg.append(label);
            }
            return group;
        });

        svg.addEventListener("keydown", (event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            const last = data.length - 1;
            const next = { ArrowLeft: Math.max((active < 0 ? last + 1 : active) - 1, 0),
                ArrowRight: Math.min(active + 1, last), Home: 0, End: last }[event.key];
            showTip(next);
        });
        svg.addEventListener("focus", () => showTip(active >= 0 ? active : data.length - 1));
        svg.addEventListener("blur", hideTip);
        frame.replaceChildren(svg);
    }

    const observer = new ResizeObserver(() => { hideTip(); render(); });
    observer.observe(frame);
    render();
    return { update(next) { data = next; hideTip(); render(); } };
}

/**
 * Horizontal bars for parts of a whole, each directly labelled with value and share.
 *   barList(container, [{ label, hint, value }])
 */
export function barList(container, rows) {
    const total = rows.reduce((sum, r) => sum + r.value, 0);
    const max = Math.max(...rows.map((r) => r.value), 1);
    container.replaceChildren(el("ul", { class: "bar-list" }, ...rows.map((row) => {
        const share = total ? Math.round((row.value / total) * 100) : 0;
        return el("li", { class: "bar-row", title: `${row.label}: ${row.value.toLocaleString()} (${share}%)` },
            el("div", { class: "bar-row-head" },
                el("span", {}, el("strong", {}, row.label), row.hint ? el("small", {}, row.hint) : null),
                el("span", { class: "bar-row-value" }, el("strong", {}, row.value.toLocaleString()), el("small", {}, `${share}%`))),
            el("div", { class: "bar-track" }, el("span", { class: "bar-fill", style: `width:${(row.value / max) * 100}%` })));
    })));
}
