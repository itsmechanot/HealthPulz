// "Trends from RHU Naval" panel: municipal totals and this barangay's trend.
// Reads only aggregated statistics from the RHU (never individual cases).

import * as api from "../common/api.js";
import { lineChart } from "../common/chart.js";
import { el } from "../common/dom.js";

const REFRESH_MS = 30_000;
const CHART_WEEKS = 12;
const SOON_MS = 11_000; // the RHU re-runs its analytics every 10 s, so new data shows up after one run

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString();

/** Starts the panel. Returns { refreshSoon } so the page can ask for an update after new data. */
export function startTrendsPanel(config, barangay) {
  let disease = config.diseases[0].disease_code;
  let pending = null;

  const select = $("trend-disease");
  select.replaceChildren(...config.diseases.map((d) => el("option", { value: d.disease_code, textContent: d.name })));
  select.addEventListener("change", () => {
    disease = select.value;
    renderChart();
  });

  async function refresh() {
    try {
      const summary = await api.fetchJson("/api/stats/summary");
      const grid = await api.fetchJson(`/api/stats/barangays?week=${summary.week.label}`);
      renderTable(summary, grid);
      await renderChart();
      $("trends-week").textContent = `${summary.week.label} (so far) vs ${summary.previous_week.label}`;
      $("trends-note").textContent = "";
    } catch {
      $("trends-note").textContent = "Trends will update when the RHU can be reached again.";
    }
  }

  function renderTable(summary, grid) {
    const ours = grid.barangays.find((b) => b.barangay_id === barangay.barangay_id)?.diseases ?? {};
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
    const name = config.diseases.find((d) => d.disease_code === disease).name;
    $("trend-chart-title").textContent = `Brgy. ${barangay.name} · ${name} · last ${weeks.length} weeks`;
    $("trend-chart").setAttribute("aria-label", `${name} cases per week in Brgy. ${barangay.name}, with alert and epidemic thresholds`);
    $("trend-scale").textContent = `max ${fmt(Math.round(max))} cases`;
    const ends = weeks.length ? [weeks[0].label, `${weeks[weeks.length - 1].label} (so far)`] : [];
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
