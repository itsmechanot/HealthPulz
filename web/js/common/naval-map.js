// Offline SVG map of Naval from /data/naval-map.json (built by tools/build_map.py).

import { el } from "./dom.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const LEVEL_RANK = { NORMAL: 0, ALERT: 1, EPIDEMIC: 2, URGENT: 3 };

let mapData = null;

async function loadMapData() {
  mapData ??= fetch("/data/naval-map.json").then((res) => {
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
export async function createNavalMap(container, { onSelect = null, ownBarangay = null, labels = true } = {}) {
  const map = await loadMapData();
  const svg = svgEl("svg", { viewBox: `0 0 ${map.width} ${map.height}`, role: "img", "aria-label": "Map of Naval, Biliran, by barangay" });

  for (const town of map.towns) svg.append(svgEl("path", { d: town.path, class: "town" }));
  for (const town of map.towns) {
    const [x, y] = town.label;
    if (x > 40 && x < map.width - 40 && y > 20 && y < map.height - 10) {
      const text = svgEl("text", { x, y, class: "town-label", "text-anchor": "middle" });
      text.textContent = town.name;
      svg.append(text);
    }
  }

  const tooltip = el("div", { className: "map-tooltip", hidden: true });
  const shapes = new Map();
  const stats = new Map(); // barangay_id -> { cases, level, rate, weekLevel }

  for (const b of map.barangays) {
    const shape = svgEl("path", { d: b.path, class: "brgy", tabindex: 0, "aria-label": b.name });
    if (b.barangay_id === ownBarangay) shape.classList.add("own");
    shape.addEventListener("mousemove", (event) => showTooltip(event, b));
    shape.addEventListener("mouseleave", () => (tooltip.hidden = true));
    if (onSelect) {
      shape.addEventListener("click", () => onSelect(b.barangay_id));
      shape.addEventListener("keydown", (e) => e.key === "Enter" && onSelect(b.barangay_id));
    } else {
      shape.addEventListener("click", (event) => showTooltip(event, b)); // phones have no hover
    }
    shapes.set(b.barangay_id, shape);
    svg.append(shape);
  }

  svg.append(svgEl("path", { d: map.naval.path, class: "naval-outline" }));
  const markers = svgEl("g");
  svg.append(markers);

  if (labels) {
    const group = svgEl("g");
    for (const b of map.barangays) {
      // The small barangays around the poblacion sit close together: smaller labels there.
      const text = svgEl("text", { x: b.center[0], y: b.center[1] - 10, class: b.area_km2 < 1 ? "brgy-label small-area" : "brgy-label" });
      text.textContent = shortName(b.name);
      group.append(text);
    }
    svg.append(group);
  }

  const title = el("span", { className: "badge", dataset: { level: "OK" }, textContent: "Municipality of Naval" });
  title.style.cssText = "position: absolute; top: 10px; left: 10px;";
  const corner = el("div", { className: "map-corner" });
  container.replaceChildren(
    el("div", { className: "map" }, svg, title, corner, tooltip),
    legend(),
    el("div", { className: "map-attribution", textContent: map.attribution }),
  );

  function showTooltip(event, b) {
    const box = container.querySelector(".map").getBoundingClientRect();
    const s = stats.get(b.barangay_id);
    const lines = [b.name];
    if (s) {
      lines.push(`${s.cases.toLocaleString()} case${s.cases === 1 ? "" : "s"} · ${s.rate.toFixed(1)} per 10,000`);
      if (s.level !== "NORMAL") lines.push(s.level);
      else if (s.weekLevel !== "NORMAL") lines.push(`${s.weekLevel} this week`);
    }
    tooltip.replaceChildren(...lines.flatMap((line, i) => (i ? [el("br"), line] : [el("strong", { textContent: line })])));
    tooltip.style.left = `${event.clientX - box.left}px`;
    tooltip.style.top = `${event.clientY - box.top}px`;
    tooltip.hidden = false;
  }

  return {
    /** diseaseCode: one disease, or null for all. */
    update(gridBarangays, diseaseCode = null) {
      stats.clear();
      for (const b of gridBarangays) {
        const cells = diseaseCode ? [b.diseases[diseaseCode]].filter(Boolean) : Object.values(b.diseases);
        const cases = cells.reduce((sum, c) => sum + c.cases, 0);
        const worst = (key) => cells.reduce((w, c) => (LEVEL_RANK[c[key] ?? "NORMAL"] > LEVEL_RANK[w] ? c[key] : w), "NORMAL");
        // A day view colors by that day's cases; week_level keeps the week's outbreak marked.
        stats.set(b.barangay_id, { cases, level: worst("level"), weekLevel: worst("week_level"), rate: (cases / b.population) * 10_000 });
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
      for (const [id, shape] of shapes) shape.classList.toggle("selected", id === barangayId);
    },

    corner,
  };
}

function legend() {
  const swatch = (color, label) => el("span", {}, Object.assign(el("span", { className: "swatch" }), { style: `background: ${color}` }), label);
  return el(
    "div",
    { className: "map-legend" },
    swatch("var(--land)", "No cases"),
    swatch("color-mix(in srgb, var(--accent) 45%, var(--land))", "Cases (darker = more per 10,000)"),
    swatch("color-mix(in srgb, var(--warn) 55%, var(--land))", "Alert"),
    swatch("color-mix(in srgb, var(--danger) 65%, var(--land))", "Epidemic"),
    swatch("color-mix(in srgb, var(--urgent) 70%, var(--land))", "Urgent"),
    el("span", { textContent: "● circle size = number of cases" }),
  );
}
