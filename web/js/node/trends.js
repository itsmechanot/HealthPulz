// Trends panel: municipal totals and this barangay's trend (aggregates only).

import * as api from "../common/api.js";
import { weekStart } from "../common/dates.js";
import { lineChart } from "../common/chart.js";
import { diseaseOptions } from "../common/diseases.js";
import { el } from "../common/dom.js";
import { createTownMap } from "../common/town-map.js";

const REFRESH_MS = 30_000;
const CHART_WEEKS = 12;
const SOON_MS = 13_000; // the RHU re-runs its analytics every 10 s, so new data shows up after one run

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString();

/** Starts the panel. Returns { refreshSoon } so the page can ask for an update after new data. */
export function startTrendsPanel(config, barangay) {
  let disease = config.diseases[0].disease_code;
  let pending = null;
  // The station's own barangay is outlined; colors show where cases and alerts are.
  const map = createTownMap($("node-map"), { ownBarangay: barangay.barangay_id });

  const select = $("trend-disease");
  select.replaceChildren(...diseaseOptions(config.diseases.filter((d) => d.tracked)));
  select.addEventListener("change", () => {
    disease = select.value;
    renderChart();
  });

  async function refresh() {
    try {
      const summary = await api.fetchJson("/api/stats/summary");
      const grid = await api.fetchJson(`/api/stats/barangays?week=${summary.week.label}`);
      renderTable(summary, grid);
      (await map).update(grid.barangays);
      await renderChart();
      $("trends-week").textContent = `${summary.week.dates} (so far) vs ${summary.previous_week.dates}`;
      $("trends-note").textContent = "";
    } catch {
      $("trends-note").textContent = "Trends will update when the RHU can be reached again.";
    }
  }

  function renderTable(summary, grid) {
    const own = grid.barangays.find((b) => b.barangay_id === barangay.barangay_id);
    const ours = own?.diseases ?? {};
    $("trends-body").replaceChildren(
      ...summary.diseases.map((d) => {
        const cell = ours[d.disease_code] ?? { cases: 0, level: "NORMAL" };
        const row = el(
          "tr",
          { title: `Show ${d.name} in the chart` },
          el("th", { scope: "row", textContent: d.name }),
          el("td", { textContent: fmt(cell.cases), dataset: { level: cell.level } }),
          el("td", { textContent: fmt(d.this_week) }),
          el("td", { textContent: fmt(d.last_week) }),
          el("td", { textContent: d.barangays_on_alert ? fmt(d.barangays_on_alert) : "–" }),
        );
        row.setAttribute("aria-selected", d.disease_code === disease);
        row.addEventListener("click", () => {
          disease = d.disease_code;
          select.value = disease;
          renderChart();
          renderTable(summary, grid);
        });
        return row;
      }),
      // Counted, but not under outbreak surveillance, so no alerts and no chart.
      el(
        "tr",
        { style: "cursor: default" },
        el("th", { scope: "row", textContent: "Other consultations" }),
        el("td", { textContent: fmt(own?.other_consultations ?? 0) }),
        el("td", { textContent: fmt(summary.other_consultations.this_week) }),
        el("td", { textContent: fmt(summary.other_consultations.last_week) }),
        el("td", { textContent: "–" }),
      ),
    );
  }

  async function renderChart() {
    const { weeks } = await api.fetchJson(api.trendUrl(disease, barangay.barangay_id, CHART_WEEKS));
    const max = lineChart(
      $("trend-chart"),
      [
        { values: weeks.map((w) => w.cases), className: "series-accent" },
        { values: weeks.map((w) => w.alert_threshold), className: "series-warn series-dashed" },
        { values: weeks.map((w) => w.epidemic_threshold), className: "series-danger series-dashed" },
      ],
      { slots: Math.max(weeks.length, 2) },
    );
    const selected = config.diseases.find((d) => d.disease_code === disease);
    const name = selected.name;
    document.querySelectorAll(".threshold-legend").forEach((item) => (item.hidden = !selected.threshold_alerts));
    $("trend-chart-title").textContent = `Brgy. ${barangay.name} · ${name} · last ${weeks.length} weeks`;
    $("trend-chart").setAttribute("aria-label", `${name} cases per week in Brgy. ${barangay.name}, with alert and epidemic thresholds`);
    $("trend-scale").textContent = `max ${fmt(Math.round(max))} cases`;
    const ends = weeks.length ? [weekStart(weeks[0]), `${weekStart(weeks[weeks.length - 1])} (so far)`] : [];
    $("trend-axis").replaceChildren(...ends.map((label) => el("span", { textContent: label })));
  }

  refresh();
  setInterval(refresh, REFRESH_MS);

  return {
    /** Refresh shortly, collapsing several requests (e.g. many STORED messages) into one. */
    refreshSoon() {
      clearTimeout(pending);
      pending = setTimeout(refresh, SOON_MS);
    },
  };
}
