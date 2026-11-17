// Offline SVG map of the municipality from /data/map.json (built by tools/build_map.py).

import { el } from "./dom.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const LEVEL_RANK = { NORMAL: 0, ALERT: 1, EPIDEMIC: 2, URGENT: 3 };
const LEVEL_LABELS = [["ALERT", "Alert"], ["EPIDEMIC", "Epidemic"], ["URGENT", "Urgent"]];

let mapData = null;

async function loadMapData() {
  mapData ??= fetch("/data/map.json").then((res) => {
    if (!res.ok) throw new Error(`Map data: HTTP ${res.status}`);
    return res.json();
  });
  return mapData;
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

const shortName = (name) => name.replace(/\s*\(Pob\.\)|\s*Pob\./g, "").replace(/^Padre /, "P. ");

/** Renders the map. Returns { update(gridBarangays, diseaseCode | null), select(barangayId), corner }:
 *  corner is an empty box in the map's upper-right corner (the dashboard puts its calendar there). */
export async function createTownMap(container, { onSelect = null, ownBarangay = null, labels = true } = {}) {
  const map = await loadMapData();
  const town = map.municipality.name;
  const svg = svgEl("svg", { viewBox: `0 0 ${map.width} ${map.height}`, role: "img", "aria-label": `Map of ${town} by barangay` });

  for (const town of map.towns) svg.append(svgEl("path", { d: town.path, class: "town" }));
  for (const town of map.towns) {
    const [x, y] = town.label;
    if (x > 40 && x < map.width - 40 && y > 20 && y < map.height - 10) {
      const text = svgEl("text", { x, y, class: "town-label", "text-anchor": "middle" });
      text.textContent = town.name;
      svg.append(text);
    }
  }

  const tooltip = el("div", { className: "map-tooltip", hidden: true, role: "dialog", "aria-live": "polite" });
  const shapes = new Map();
  const stats = new Map(); // barangay_id -> { cases, level, weekLevel, rate, population, levels }
  let pinnedId = null;

  for (const b of map.barangays) {
    const shape = svgEl("path", { d: b.path, class: "brgy", tabindex: 0, role: "button", "aria-label": b.name });
    if (b.barangay_id === ownBarangay) shape.classList.add("own");
    shape.addEventListener("pointermove", (event) => pinnedId !== b.barangay_id && showTooltip(event, b, false));
    shape.addEventListener("pointerleave", () => pinnedId !== b.barangay_id && (tooltip.hidden = true));
    shape.addEventListener("click", (event) => {
      pinnedId = b.barangay_id; // a click (or tap on a phone) keeps the details open
      showTooltip(event, b, true);
      onSelect?.(b.barangay_id);
    });
    shape.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        shape.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      } else if (event.key === "Escape") dismissTooltip();
    });
    shapes.set(b.barangay_id, shape);
    svg.append(shape);
  }

  svg.append(svgEl("path", { d: map.municipality.path, class: "town-outline" }));
  const markers = svgEl("g", { "aria-hidden": "true" });
  svg.append(markers);

  if (labels) {
    const group = svgEl("g", { class: "barangay-labels", "aria-hidden": "true" });
    for (const b of map.barangays) {
      // The small barangays around the poblacion sit close together: smaller labels there.
      const text = svgEl("text", { x: b.center[0], y: b.center[1] - 10, class: b.area_km2 < 1 ? "brgy-label small-area" : "brgy-label" });
      text.textContent = shortName(b.name);
      group.append(text);
    }
    svg.append(group);
  }

  const title = el("span", { className: "badge", dataset: { level: "OK" }, textContent: `Municipality of ${town}` });
  title.style.cssText = "position: absolute; top: 10px; left: 10px;";
  const corner = el("div", { className: "map-corner" });
  const frame = el("div", { className: "map" }, svg, title, corner, tooltip);
  container.replaceChildren(frame, legend(), el("div", { className: "map-attribution", textContent: map.attribution }));

  tooltip.addEventListener("click", (event) => event.target.closest("[data-map-tooltip-close]") && dismissTooltip());
  container.addEventListener("click", (event) => {
    if (pinnedId && !event.target.closest(".brgy") && !tooltip.contains(event.target)) dismissTooltip();
  });

  function dismissTooltip() {
    pinnedId = null;
    tooltip.hidden = true;
  }

  function showTooltip(event, b, pinned) {
    const box = frame.getBoundingClientRect();
    const s = stats.get(b.barangay_id);
    const row = (dot, label, value) =>
      el("div", { className: "map-tooltip-row" }, el("span", {}, indicator(dot), ` ${label}`), el("strong", { textContent: value }));
    const rows = [
      el("div", { className: "map-tooltip-heading" },
        el("strong", { textContent: b.name }),
        el("button", { type: "button", className: "map-tooltip-close", dataset: { mapTooltipClose: "" }, "aria-label": "Close barangay details", textContent: "×", hidden: !pinned }),
      ),
      el("p", { className: "map-tooltip-population", textContent: `Population: ${s ? s.population.toLocaleString() : "–"}` }),
    ];
    if (s) {
      rows.push(
        el("p", { className: "map-tooltip-subheading", textContent: "Surveillance summary" }),
        row("cases", "Total cases", s.cases.toLocaleString()),
        row("cases", "Per 10,000 people", s.rate.toFixed(1)),
        ...LEVEL_LABELS.map(([level, label]) => row(level.toLowerCase(), `${label} diseases`, String(s.levels[level]))),
      );
      // A day view: the week's outbreak still shows even when that day had no cases.
      if (s.level === "NORMAL" && s.weekLevel !== "NORMAL") rows.push(el("p", { className: "map-tooltip-population", textContent: `${s.weekLevel} this week` }));
    }
    tooltip.replaceChildren(...rows);
    tooltip.dataset.pinned = String(pinned);
    tooltip.hidden = false;

    const shapeBox = event.currentTarget?.getBoundingClientRect?.();
    const atX = event.clientX ? event.clientX - box.left : shapeBox ? shapeBox.left + shapeBox.width / 2 - box.left : 0;
    const atY = event.clientY ? event.clientY - box.top : shapeBox ? shapeBox.top + shapeBox.height / 2 - box.top : 0;
    const below = atY + 12;
    tooltip.style.left = `${Math.max(8, Math.min(atX + 12, box.width - tooltip.offsetWidth - 8))}px`;
    tooltip.style.top = `${below + tooltip.offsetHeight <= box.height - 8 ? below : Math.max(8, atY - tooltip.offsetHeight - 12)}px`;
  }

  return {
    /** diseaseCode: one disease, or null for all. */
    update(gridBarangays, diseaseCode = null) {
      stats.clear();
      for (const b of gridBarangays) {
        const cells = diseaseCode ? [b.diseases[diseaseCode]].filter(Boolean) : Object.values(b.diseases);
        const cases = cells.reduce((sum, c) => sum + c.cases, 0);
        const worst = (key) => cells.reduce((w, c) => (LEVEL_RANK[c[key] ?? "NORMAL"] > LEVEL_RANK[w] ? c[key] : w), "NORMAL");
        const levels = { ALERT: 0, EPIDEMIC: 0, URGENT: 0 };
        for (const c of cells) if (c.level in levels) levels[c.level] += 1;
        // A day view colors by that day's cases; week_level keeps the week's outbreak marked.
        stats.set(b.barangay_id, {
          cases, levels, population: b.population, level: worst("level"), weekLevel: worst("week_level"), rate: (cases / b.population) * 10_000,
        });
        shapes.get(b.barangay_id)?.setAttribute("aria-label", `${b.name}, ${cases} case${cases === 1 ? "" : "s"}`);
      }
      const maxRate = Math.max(1e-9, ...[...stats.values()].map((s) => s.rate));

      for (const [id, shape] of shapes) {
        const s = stats.get(id) ?? { cases: 0, level: "NORMAL", rate: 0 };
        if (s.level !== "NORMAL") shape.dataset.level = s.level;
        else if (s.cases > 0) shape.dataset.level = "CASES";
        else delete shape.dataset.level;
        shape.style.setProperty("--intensity", (0.18 + 0.6 * (s.rate / maxRate)).toFixed(2));
      }

      // Markers: bigger circle = more cases; drawn largest first so small ones stay visible.
      // A barangay on alert that week keeps a marker on a day view, even with no cases that day.
      const withCases = map.barangays
        .map((b) => ({ b, s: stats.get(b.barangay_id) }))
        .filter(({ s }) => s && (s.cases > 0 || s.weekLevel !== "NORMAL"))
        .sort((a, b) => b.s.cases - a.s.cases);
      markers.replaceChildren(
        ...withCases.map(({ b, s }) => {
          const circle = svgEl("circle", {
            cx: b.center[0], cy: b.center[1] + 6, r: Math.min(26, 5 + Math.sqrt(s.cases) * 3.2), class: "marker",
          });
          const level = s.level !== "NORMAL" ? s.level : s.weekLevel;
          if (level !== "NORMAL") circle.dataset.level = level;
          if (level === "EPIDEMIC" || level === "URGENT") circle.classList.add("pulse");
          return circle;
        }),
      );
    },

    select(barangayId) {
      for (const [id, shape] of shapes) {
        shape.classList.toggle("selected", id === barangayId);
        shape.setAttribute("aria-pressed", String(id === barangayId));
      }
      if (!barangayId) dismissTooltip();
    },

    corner,
  };
}

function indicator(kind) {
  return el("span", { className: `map-status-dot map-status-${kind}`, "aria-hidden": "true" });
}

function legend() {
  const swatch = (kind, label) =>
    el("span", { className: "map-legend-item" }, el("span", { className: `map-legend-swatch map-status-${kind}`, "aria-hidden": "true" }), label);
  return el(
    "div",
    { className: "map-legend", "aria-label": "Map legend" },
    swatch("none", "No cases"),
    swatch("cases", "Cases (darker = more per 10,000)"),
    swatch("alert", "Alert"),
    swatch("epidemic", "Epidemic"),
    swatch("urgent", "Urgent"),
    el("span", { className: "map-legend-note", textContent: "Alert and epidemic use weekly thresholds; an urgent disease alerts on a single case. Bigger circle = more cases." }),
  );
}
