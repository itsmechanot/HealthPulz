// RHU Dashboard: disease cards, alerts (with actions), trend chart and barangay table.
// Reads the /api/stats and /api/alerts endpoints and refreshes every 10 s.

import * as api from "../common/api.js";
import { lineChart } from "../common/chart.js";
import { el } from "../common/dom.js";

const REFRESH_MS = 10_000;
const SPARKLINE_WEEKS = 12;

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString();

const state = {
  disease: null, // selected disease code (cards, chart)
  barangay: "", // "" = all of Naval
  weeks: 26,
  gridWeek: "current",
  alertFilter: "active",
};
let config;
let summary;

start();

async function start() {
  try {
    config = await api.getConfig();
  } catch (error) {
    setConnection("offline", `Cannot reach the RHU server (${error.message})`);
    return;
  }
  state.disease = config.diseases[0].disease_code;
  $("trend-barangay").append(...config.barangays.map((b) => el("option", { value: b.barangay_id, textContent: b.name })));
  wireControls();
  await refresh();
  setInterval(refresh, REFRESH_MS);
}

function wireControls() {
  $("trend-barangay").addEventListener("change", (e) => {
    state.barangay = e.target.value;
    renderAll();
  });
  $("trend-weeks").addEventListener("change", (e) => {
    state.weeks = Number(e.target.value);
    renderAll();
  });
  pressGroup("[data-grid-week]", (button) => {
    state.gridWeek = button.dataset.gridWeek;
    renderAll();
  });
  pressGroup("[data-alert-filter]", (button) => {
    state.alertFilter = button.dataset.alertFilter;
    renderAlerts();
  });
}

/** A group of toggle buttons where exactly one is pressed. */
function pressGroup(selector, onPress) {
  const buttons = document.querySelectorAll(selector);
  buttons.forEach((button) =>
    button.addEventListener("click", () => {
      buttons.forEach((b) => b.setAttribute("aria-pressed", b === button));
      onPress(button);
    }),
  );
}

async function refresh() {
  try {
    summary = await api.fetchJson("/api/stats/summary");
    await Promise.all([renderCards(), renderAlerts(), renderTrend(), renderGrid()]);
    renderHeader();
    setConnection("online", "Live");
  } catch (error) {
    setConnection("offline", "Disconnected, retrying…");
    console.warn("Dashboard refresh failed", error);
  }
}

function renderAll() {
  renderCards();
  renderTrend();
  renderGrid();
}

function setConnection(state, text) {
  $("connection").dataset.state = state;
  $("connection").textContent = text;
}

// ---- header -------------------------------------------------------------------

function renderHeader() {
  const { week, previous_week: prev } = summary;
  $("week-label").textContent =
    `This week ${week.label} (from ${formatDate(week.start)}, so far) · compared with ${prev.label}`;
  const analyzed = summary.analyzed_at ? new Date(summary.analyzed_at).toLocaleTimeString() : "not yet";
  $("updated").textContent = `Data analyzed ${analyzed} · refreshes every 10 s`;
}

// ---- disease cards --------------------------------------------------------------

async function renderCards() {
  const trends = await Promise.all(
    summary.diseases.map((d) => api.fetchJson(api.trendUrl(d.disease_code, state.barangay, SPARKLINE_WEEKS))),
  );
  $("disease-cards").replaceChildren(
    ...summary.diseases.map((d, i) => {
      const spark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      spark.setAttribute("aria-hidden", "true");
      lineChart(spark, [{ values: trends[i].weeks.map((w) => w.cases), className: "series-accent" }], { slots: SPARKLINE_WEEKS });

      const card = el(
        "button",
        { type: "button", className: "disease-card", title: `Show ${d.name} in the chart` },
        el("span", { className: "small muted", textContent: d.name }),
        el("span", { className: "stat-value", textContent: fmt(d.this_week) }),
        el("span", { className: "small muted", textContent: `this week so far · last week ${fmt(d.last_week)}` }),
        spark,
        d.barangays_on_alert
          ? el("span", {
              className: "badge",
              dataset: { level: "ALERT" },
              textContent: `${d.barangays_on_alert} barangay${d.barangays_on_alert > 1 ? "s" : ""} on alert`,
            })
          : el("span", { className: "badge", textContent: "No alerts" }),
      );
      card.setAttribute("aria-pressed", d.disease_code === state.disease);
      card.addEventListener("click", () => {
        state.disease = d.disease_code;
        renderAll();
      });
      return card;
    }),
  );
}

// ---- alerts -----------------------------------------------------------------------

async function renderAlerts() {
  const query = state.alertFilter === "RESOLVED" ? "?status=RESOLVED" : "";
  let alerts = await api.fetchJson(`/api/alerts${query}`);
  if (state.alertFilter === "active") alerts = alerts.filter((a) => a.status !== "RESOLVED");

  if (!alerts.length) {
    const text = state.alertFilter === "active" ? "No active alerts. All barangays are within normal levels." : "No alerts.";
    $("alerts").replaceChildren(el("p", { className: "empty", textContent: text }));
    return;
  }
  $("alerts").replaceChildren(...alerts.map(alertRow));
}

function alertRow(alert) {
  const actions = el("div", { className: "actions" });
  const action = (label, status, primary = false) => {
    const button = el("button", { type: "button", className: `btn btn-small ${primary ? "btn-primary" : "btn-ghost"}`, textContent: label });
    button.addEventListener("click", async () => {
      button.disabled = true;
      await api.fetchJson(`/api/alerts/${alert.alert_id}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      await refresh();
    });
    return button;
  };
  if (alert.status === "OPEN") actions.append(action("Acknowledge", "ACKNOWLEDGED", true));
  if (alert.status !== "RESOLVED") actions.append(action("Resolve", "RESOLVED"));
  else actions.append(action("Reopen", "OPEN"));

  const showTrend = el("button", { type: "button", className: "btn btn-small btn-ghost", textContent: "Show trend" });
  showTrend.addEventListener("click", () => {
    state.disease = alert.disease_code;
    state.barangay = alert.barangay_id;
    $("trend-barangay").value = alert.barangay_id;
    renderAll();
    $("trend-title").scrollIntoView({ behavior: "smooth" });
  });
  actions.append(showTrend);

  const statusText = { OPEN: "Open", ACKNOWLEDGED: "Acknowledged by RHU", RESOLVED: "Resolved" }[alert.status];
  return el(
    "article",
    { className: "alert-row", dataset: { level: alert.level, status: alert.status } },
    el(
      "div",
      {},
      el("span", { className: "badge", dataset: { level: alert.level }, textContent: alert.level }),
      " ",
      el("strong", { textContent: `${diseaseName(alert.disease_code)} · Brgy. ${barangayName(alert.barangay_id)}` }),
      el("span", { className: "muted small", textContent: ` · week ${alert.iso_year}-W${String(alert.iso_week).padStart(2, "0")}` }),
    ),
    el("div", { className: "small", textContent: alert.message }),
    el("div", {
      className: "muted small",
      textContent:
        `${statusText} · raised ${new Date(alert.created_at).toLocaleString()} · ` +
        `sent to ${alert.nodes_delivered} station${alert.nodes_delivered === 1 ? "" : "s"}, ` +
        `${alert.nodes_acknowledged} acknowledged`,
    }),
    actions,
  );
}

// ---- trend chart --------------------------------------------------------------------

async function renderTrend() {
  const data = await api.fetchJson(api.trendUrl(state.disease, state.barangay, state.weeks));
  const weeks = data.weeks;
  const withThresholds = Boolean(state.barangay);
  const series = [{ values: weeks.map((w) => w.cases), className: "series-accent" }];
  if (withThresholds) {
    series.push(
      { values: weeks.map((w) => w.alert_threshold), className: "series-warn series-dashed" },
      { values: weeks.map((w) => w.epidemic_threshold), className: "series-danger series-dashed" },
    );
  }
  const max = lineChart($("trend-chart"), series, { slots: Math.max(weeks.length, 2) });

  const area = state.barangay ? `Brgy. ${barangayName(state.barangay)}` : "all of Naval";
  $("trend-title").textContent = `Trend · ${diseaseName(state.disease)} · ${area}`;
  $("trend-chart").setAttribute("aria-label", `${diseaseName(state.disease)} cases per week in ${area}, last ${weeks.length} weeks`);
  $("trend-scale").textContent = `max ${fmt(Math.round(max))} cases`;
  document.querySelectorAll(".threshold-legend").forEach((item) => (item.hidden = !withThresholds));
  $("trend-note").textContent = withThresholds
    ? "Thresholds come from the same weeks in past years (mean + 2 SD for alert, + 3 SD for epidemic). The last week is still in progress."
    : "Choose a barangay to compare with its alert and epidemic thresholds. The last week is still in progress.";

  const labels = weeks.length ? [weeks[0], weeks[Math.floor(weeks.length / 2)], weeks[weeks.length - 1]] : [];
  $("trend-axis").replaceChildren(...labels.map((w, i) => el("span", { textContent: i === 2 ? `${w.label} (so far)` : w.label })));
}

// ---- barangay table --------------------------------------------------------------

async function renderGrid() {
  const week = state.gridWeek === "current" ? summary.week : summary.previous_week;
  const data = await api.fetchJson(`/api/stats/barangays?week=${week.label}`);
  $("grid-title").textContent = `Barangays · ${week.label}${state.gridWeek === "current" ? " (so far)" : ""}`;

  $("grid-head").replaceChildren(
    el("th", { scope: "col", textContent: "Barangay" }),
    ...config.diseases.map((d) => el("th", { scope: "col", textContent: d.name, title: d.name })),
    el("th", { scope: "col", textContent: "Total" }),
    el("th", { scope: "col", textContent: "Per 10,000" }),
  );

  const rows = [...data.barangays].sort((a, b) => b.rate_per_10k - a.rate_per_10k || a.name.localeCompare(b.name));
  $("grid-body").replaceChildren(
    ...rows.map((b) => {
      const row = el(
        "tr",
        { title: `Show the trend for Brgy. ${b.name}` },
        el("th", { scope: "row", textContent: b.name }),
        ...config.diseases.map((d) => {
          const cell = b.diseases[d.disease_code] ?? { cases: 0, level: "NORMAL" };
          return el("td", {
            textContent: fmt(cell.cases),
            className: cell.cases ? "" : "zero",
            dataset: { level: cell.level },
            title: cell.alert_threshold == null ? "No threshold yet" : `Alert above ${cell.alert_threshold}, epidemic above ${cell.epidemic_threshold}`,
          });
        }),
        el("td", { textContent: fmt(b.total) }),
        el("td", { textContent: b.rate_per_10k.toFixed(1) }),
      );
      row.setAttribute("aria-selected", b.barangay_id === state.barangay);
      row.addEventListener("click", () => {
        state.barangay = b.barangay_id;
        $("trend-barangay").value = b.barangay_id;
        renderAll();
        $("trend-title").scrollIntoView({ behavior: "smooth" });
      });
      return row;
    }),
  );
}

// ---- helpers -------------------------------------------------------------------------

const diseaseName = (code) => config.diseases.find((d) => d.disease_code === code)?.name ?? code;
const barangayName = (id) => config.barangays.find((b) => b.barangay_id === id)?.name ?? id;
const formatDate = (iso) => new Date(`${iso}T00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
