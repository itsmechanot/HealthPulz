// RHU Dashboard: five screens (Overview, Map, Alerts, Trends, Barangays) over the
// /api/stats and /api/alerts endpoints, refreshed every 10 s (the analytics interval).

import * as api from "../common/api.js";
import { lineChart } from "../common/chart.js";
import { el } from "../common/dom.js";
import { createNavalMap } from "../common/naval-map.js";
import { createRouter, setupMenu } from "../common/shell.js";

const REFRESH_MS = 10_000;
const SPARKLINE_WEEKS = 12;
const OVERVIEW_ALERTS = 4;
const SCREENS = {
  overview: ["Overview", "Illness situation in Naval, Biliran"],
  map: ["Map", "Cases by barangay, Municipality of Naval"],
  alerts: ["Alerts", "Outbreaks detected by the parallel analytics"],
  trends: ["Trends", "Weekly cases compared with outbreak thresholds"],
  barangays: ["Barangays", "Every barangay and disease for one week"],
};

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString();

const state = {
  disease: null, // Trends screen
  barangay: "", // Trends screen: "" = all of Naval
  weeks: 26,
  gridWeek: "current",
  alertFilter: "active",
  mapDisease: null, // null = all diseases
  mapWeek: "current",
  selected: null, // barangay selected on the map
};
const data = { summary: null, grids: {}, alerts: [], nodes: [] };
let config;
let router;
let overviewMap;
let bigMap;

start();

async function start() {
  try {
    config = await api.getConfig();
  } catch (error) {
    setConnection("offline", "RHU unreachable");
    return;
  }
  state.disease = config.diseases[0].disease_code;
  setupMenu();
  buildControls();
  [overviewMap, bigMap] = await Promise.all([
    createNavalMap($("ov-map"), { labels: false, onSelect: (id) => selectBarangay(id, true) }),
    createNavalMap($("big-map"), { onSelect: (id) => selectBarangay(id, false) }),
  ]);
  watchStations();
  router = createRouter("overview", (screen) => {
    const [title, subtitle] = SCREENS[screen];
    $("screen-title").textContent = title;
    $("screen-subtitle").textContent = subtitle;
    document.title = `HealthPulz · ${title}`;
    if (data.summary) renderScreen(screen);
  });
  await refresh();
  router.start();
  setInterval(refresh, REFRESH_MS);
}

// ---- data -----------------------------------------------------------------------

async function refresh() {
  try {
    data.summary = await api.fetchJson("/api/stats/summary");
    const [current, previous, alerts] = await Promise.all([
      api.fetchJson(`/api/stats/barangays?week=${data.summary.week.label}`),
      api.fetchJson(`/api/stats/barangays?week=${data.summary.previous_week.label}`),
      api.fetchJson("/api/alerts"),
    ]);
    data.grids = { current, previous };
    data.alerts = alerts;
    renderChrome();
    if (router) renderScreen(router.current());
    setConnection("online", "Live");
  } catch (error) {
    setConnection("offline", "Reconnecting…");
    console.warn("Dashboard refresh failed", error);
  }
}

/** Stations online come from the live monitor feed (one snapshot per second). */
function watchStations() {
  const ws = new WebSocket(api.wsUrl("/ws/monitor"));
  ws.onmessage = (event) => {
    data.nodes = JSON.parse(event.data).nodes;
    renderStations();
  };
  ws.onclose = () => setTimeout(watchStations, 3000);
}

function setConnection(stateName, text) {
  $("connection").dataset.state = stateName;
  $("connection").textContent = text;
}

const activeAlerts = () => data.alerts.filter((a) => a.status !== "RESOLVED");

// ---- chrome (top bar, sidebar badge) ------------------------------------------------

function renderChrome() {
  const { week } = data.summary;
  $("week-chip").textContent = `Week ${week.label} · from ${formatDate(week.start)} (so far)`;
  const open = activeAlerts().filter((a) => a.status === "OPEN").length;
  $("nav-alert-count").hidden = open === 0;
  $("nav-alert-count").textContent = open;
}

function renderScreen(screen) {
  ({ overview: renderOverview, map: renderMapScreen, alerts: renderAlertsScreen, trends: renderTrends, barangays: renderGrid })[screen]();
}

// ---- Overview ------------------------------------------------------------------------

function renderOverview() {
  const { totals, diseases } = data.summary;
  const grid = data.grids.current.barangays;
  const active = activeAlerts();

  $("kpi-cases").textContent = fmt(totals.this_week);
  $("kpi-cases-sub").textContent = `Last week: ${fmt(totals.last_week)} cases`;
  $("kpi-alerts").textContent = fmt(active.length);
  const epidemic = active.filter((a) => a.level === "EPIDEMIC").length;
  $("kpi-alerts-sub").textContent = active.length ? `${epidemic} at epidemic level · ${active.filter((a) => a.status === "OPEN").length} not yet acknowledged` : "All barangays within normal levels";
  const withCases = grid.filter((b) => b.total > 0).length;
  const onAlert = grid.filter((b) => Object.values(b.diseases).some((c) => c.level !== "NORMAL")).length;
  $("kpi-barangays").textContent = `${withCases} / ${grid.length}`;
  $("kpi-barangays-sub").textContent = `${onAlert} over a threshold this week`;
  renderStations();

  overviewMap.update(grid);
  const latest = active.slice(0, OVERVIEW_ALERTS);
  $("ov-alerts").replaceChildren(
    ...(latest.length ? latest.map((a) => alertRow(a, { compact: true })) : [el("p", { className: "empty", textContent: "No active alerts. All barangays are within normal levels." })]),
  );
  renderDiseaseCards(diseases);
}

function renderStations() {
  if (!$("kpi-stations")) return;
  const online = data.nodes.filter((n) => n.status === "ONLINE").length;
  $("kpi-stations").textContent = fmt(online);
  const barangays = new Set(data.nodes.map((n) => n.barangay_id)).size;
  $("kpi-stations-sub").textContent = `${fmt(data.nodes.length)} registered · ${barangays} of 26 barangays`;
}

async function renderDiseaseCards(diseases) {
  const trends = await Promise.all(diseases.map((d) => api.fetchJson(api.trendUrl(d.disease_code, "", SPARKLINE_WEEKS))));
  $("disease-cards").replaceChildren(
    ...diseases.map((d, i) => {
      const spark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      spark.setAttribute("aria-hidden", "true");
      lineChart(spark, [{ values: trends[i].weeks.map((w) => w.cases), className: "series-accent" }], { slots: SPARKLINE_WEEKS });
      const card = el(
        "button",
        { type: "button", className: "disease-card", title: `Open the ${d.name} trend` },
        el("span", { className: "small muted", textContent: d.name }),
        el("span", { className: "stat-value", textContent: fmt(d.this_week) }),
        el("span", { className: "small muted", textContent: `this week so far · last week ${fmt(d.last_week)}` }),
        spark,
        d.barangays_on_alert
          ? el("span", { className: "badge", dataset: { level: "ALERT" }, textContent: `${d.barangays_on_alert} barangay${d.barangays_on_alert > 1 ? "s" : ""} on alert` })
          : el("span", { className: "badge", textContent: "No alerts" }),
      );
      card.addEventListener("click", () => {
        state.disease = d.disease_code;
        router.go("trends");
      });
      return card;
    }),
  );
}

// ---- Map -------------------------------------------------------------------------------

function renderMapScreen() {
  const chips = [{ code: null, name: "All diseases" }, ...config.diseases.map((d) => ({ code: d.disease_code, name: d.name }))];
  $("map-diseases").replaceChildren(
    ...chips.map(({ code, name }) => {
      const chip = el("button", { type: "button", className: "filter-chip", textContent: name });
      chip.setAttribute("aria-pressed", code === state.mapDisease);
      chip.addEventListener("click", () => {
        state.mapDisease = code;
        renderMapScreen();
      });
      return chip;
    }),
  );
  bigMap.update(data.grids[state.mapWeek].barangays, state.mapDisease);
  bigMap.select(state.selected);
  renderDetail();
}

function selectBarangay(id, fromOverview) {
  state.selected = id;
  if (fromOverview) router.go("map");
  else {
    bigMap.select(id);
    renderDetail();
  }
}

function renderDetail() {
  if (!state.selected) return;
  const b = data.grids[state.mapWeek].barangays.find((x) => x.barangay_id === state.selected);
  const meta = config.barangays.find((x) => x.barangay_id === state.selected);
  const week = state.mapWeek === "current" ? `${data.summary.week.label} (so far)` : data.summary.previous_week.label;
  const worst = Object.values(b.diseases).reduce((w, c) => (c.level === "EPIDEMIC" || (c.level === "ALERT" && w !== "EPIDEMIC") ? c.level : w), "NORMAL");

  const rows = config.diseases.map((d) => {
    const cell = b.diseases[d.disease_code] ?? { cases: 0, level: "NORMAL" };
    return el(
      "div",
      {},
      el("span", { textContent: d.name }),
      el("span", {}, cell.level !== "NORMAL" ? el("span", { className: "badge", dataset: { level: cell.level }, textContent: cell.level }) : "", ` ${fmt(cell.cases)}`),
    );
  });
  const trendBtn = el("button", { type: "button", className: "btn btn-ghost btn-small", textContent: "Open trend" });
  trendBtn.addEventListener("click", () => {
    state.barangay = b.barangay_id;
    $("trend-barangay").value = b.barangay_id;
    router.go("trends");
  });

  $("map-detail").replaceChildren(
    el("div", { className: "station-header" },
      el("span", { className: "avatar", textContent: initials(b.name) }),
      el("div", { className: "grow" }, el("h3", { textContent: `Brgy. ${b.name}`, style: "font-size: 1.05rem" }), el("span", { className: "muted small", textContent: `Week ${week}` })),
      el("span", { className: "badge", dataset: { level: worst === "NORMAL" ? "OK" : worst }, textContent: worst === "NORMAL" ? "Normal" : worst }),
    ),
    el("div", { className: "stats three" },
      el("div", { className: "stat" }, el("div", { className: "stat-value", textContent: fmt(b.total) }), el("div", { className: "stat-label", textContent: "Cases" })),
      el("div", { className: "stat" }, el("div", { className: "stat-value", textContent: b.rate_per_10k.toFixed(1) }), el("div", { className: "stat-label", textContent: "Per 10,000" })),
      el("div", { className: "stat" }, el("div", { className: "stat-value", textContent: fmt(b.population) }), el("div", { className: "stat-label", textContent: "Population*" })),
    ),
    el("div", { className: "detail-list" }, ...rows),
    el("p", { className: "muted small", style: "margin: 0", textContent: `Neighbors: ${meta.neighbors.map(barangayName).join(", ") || "none (island)"}. They also receive this barangay's alerts. *Synthetic population.` }),
    trendBtn,
  );
}

// ---- Alerts ------------------------------------------------------------------------------

function renderAlertsScreen() {
  let alerts = data.alerts;
  if (state.alertFilter === "active") alerts = activeAlerts();
  if (state.alertFilter === "RESOLVED") alerts = alerts.filter((a) => a.status === "RESOLVED");
  const empty = state.alertFilter === "active" ? "No active alerts. All barangays are within normal levels." : "No alerts.";
  $("alerts").replaceChildren(...(alerts.length ? alerts.map((a) => alertRow(a)) : [el("p", { className: "empty", textContent: empty })]));
}

function alertRow(alert, { compact = false } = {}) {
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
    router.go("trends");
  });
  actions.append(showTrend);

  const statusText = { OPEN: "Open", ACKNOWLEDGED: "Acknowledged by RHU", RESOLVED: "Resolved" }[alert.status];
  const week = `${alert.iso_year}-W${String(alert.iso_week).padStart(2, "0")}`;
  return el(
    "article",
    { className: "alert-row", dataset: { level: alert.level, status: alert.status } },
    el("div", {},
      el("span", { className: "badge", dataset: { level: alert.level }, textContent: alert.level }), " ",
      el("strong", { textContent: `${diseaseName(alert.disease_code)} · Brgy. ${barangayName(alert.barangay_id)}` }),
      el("span", { className: "muted small", textContent: ` · week ${week}` }),
    ),
    compact ? null : el("div", { className: "small", textContent: alert.message }),
    el("div", {
      className: "muted small",
      textContent: compact
        ? `${alert.observed} cases (threshold ${alert.threshold}) · ${statusText}`
        : `${statusText} · raised ${new Date(alert.created_at).toLocaleString()} · sent to ${alert.nodes_delivered} station${alert.nodes_delivered === 1 ? "" : "s"}, ${alert.nodes_acknowledged} acknowledged`,
    }),
    actions,
  );
}

// ---- Trends ------------------------------------------------------------------------------

async function renderTrends() {
  $("trend-diseases").replaceChildren(
    ...config.diseases.map((d) => {
      const chip = el("button", { type: "button", className: "filter-chip", textContent: d.name });
      chip.setAttribute("aria-pressed", d.disease_code === state.disease);
      chip.addEventListener("click", () => {
        state.disease = d.disease_code;
        renderTrends();
      });
      return chip;
    }),
  );
  $("trend-barangay").value = state.barangay;

  const { weeks } = await api.fetchJson(api.trendUrl(state.disease, state.barangay, state.weeks));
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
  $("trend-title").textContent = `${diseaseName(state.disease)} · ${area}`;
  $("trend-chart").setAttribute("aria-label", `${diseaseName(state.disease)} cases per week in ${area}, last ${weeks.length} weeks`);
  $("trend-scale").textContent = `max ${fmt(Math.round(max))} cases`;
  document.querySelectorAll(".threshold-legend").forEach((item) => (item.hidden = !withThresholds));
  $("trend-note").textContent = withThresholds
    ? "Thresholds come from the same weeks in past years (mean + 2 SD for alert, + 3 SD for epidemic). The last week is still in progress."
    : "Choose a barangay to compare with its alert and epidemic thresholds. The last week is still in progress.";
  const labels = weeks.length ? [weeks[0], weeks[Math.floor(weeks.length / 2)], weeks.at(-1)] : [];
  $("trend-axis").replaceChildren(...labels.map((w, i) => el("span", { textContent: i === 2 ? `${w.label} (so far)` : w.label })));
}

// ---- Barangays -----------------------------------------------------------------------------

function renderGrid() {
  const week = state.gridWeek === "current" ? data.summary.week : data.summary.previous_week;
  $("grid-title").textContent = `Barangays · ${week.label}${state.gridWeek === "current" ? " (so far)" : ""}`;
  $("grid-head").replaceChildren(
    el("th", { scope: "col", textContent: "Barangay" }),
    ...config.diseases.map((d) => el("th", { scope: "col", textContent: d.name })),
    el("th", { scope: "col", textContent: "Total" }),
    el("th", { scope: "col", textContent: "Per 10,000" }),
  );
  const rows = [...data.grids[state.gridWeek].barangays].sort((a, b) => b.rate_per_10k - a.rate_per_10k || a.name.localeCompare(b.name));
  $("grid-body").replaceChildren(
    ...rows.map((b) => {
      const row = el(
        "tr",
        { title: `Show Brgy. ${b.name} on the map`, tabIndex: 0 },
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
      const open = () => {
        state.selected = b.barangay_id;
        state.mapWeek = state.gridWeek;
        setPressed("[data-map-week]", state.mapWeek, "mapWeek");
        router.go("map");
      };
      row.addEventListener("click", open);
      row.addEventListener("keydown", (e) => e.key === "Enter" && open());
      return row;
    }),
  );
}

// ---- controls & helpers ---------------------------------------------------------------------

function buildControls() {
  const sorted = [...config.barangays].sort((a, b) => a.name.localeCompare(b.name));
  $("trend-barangay").append(...sorted.map((b) => el("option", { value: b.barangay_id, textContent: b.name })));
  $("trend-barangay").addEventListener("change", (e) => {
    state.barangay = e.target.value;
    renderTrends();
  });
  $("trend-weeks").addEventListener("change", (e) => {
    state.weeks = Number(e.target.value);
    renderTrends();
  });
  pressGroup("[data-alert-filter]", "alertFilter", renderAlertsScreen);
  pressGroup("[data-grid-week]", "gridWeek", renderGrid);
  pressGroup("[data-map-week]", "mapWeek", renderMapScreen);
}

/** Toggle buttons where exactly one is pressed; the pressed value goes into state[key]. */
function pressGroup(selector, key, render) {
  document.querySelectorAll(selector).forEach((button) =>
    button.addEventListener("click", () => {
      state[key] = Object.values(button.dataset)[0];
      setPressed(selector, state[key], key);
      render();
    }),
  );
}

function setPressed(selector, value) {
  document.querySelectorAll(selector).forEach((b) => b.setAttribute("aria-pressed", Object.values(b.dataset)[0] === value));
}

const diseaseName = (code) => config.diseases.find((d) => d.disease_code === code)?.name ?? code;
const barangayName = (id) => config.barangays.find((b) => b.barangay_id === id)?.name ?? id;
const formatDate = (iso) => new Date(`${iso}T00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

function initials(name) {
  const words = name.replace(/\(.*?\)/g, "").trim().split(/\s+/);
  return (words.length > 1 ? words[0][0] + words[1][0] : name.slice(0, 2)).toUpperCase();
}
