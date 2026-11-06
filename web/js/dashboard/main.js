// RHU Dashboard: Overview, Map, Alerts, Trends and Barangays over the /api/stats and /api/alerts
// endpoints, refreshed every 10 s (the analytics interval), plus BHU Accounts (accounts.js).

import * as api from "../common/api.js";
import { lineChart } from "../common/chart.js";
import { diseaseOptions } from "../common/diseases.js";
import { el } from "../common/dom.js";
import { createNavalMap } from "../common/naval-map.js";
import { createRouter, setupLogout, setupMenu } from "../common/shell.js";
import { createAccountsScreen } from "./accounts.js";

const REFRESH_MS = 10_000;
const SPARKLINE_WEEKS = 12;
const OVERVIEW_ALERTS = 4;
const LEVEL_RANK = { NORMAL: 0, ALERT: 1, EPIDEMIC: 2, URGENT: 3 };
const SCREENS = {
  overview: ["Overview", "Illness situation in Naval, Biliran"],
  map: ["Map", "Cases by barangay, Municipality of Naval"],
  alerts: ["Alerts", "Outbreaks detected by the parallel analytics"],
  trends: ["Trends", "Weekly cases compared with outbreak thresholds"],
  barangays: ["Barangays", "Every barangay and disease for one week"],
  accounts: ["BHU Accounts", "Give each Barangay Health Unit its password"],
};

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString();

const state = {
  disease: null, // Trends screen
  barangay: "", // Trends screen: "" = all of Naval
  weeks: 26,
  gridWeek: "current",
  gridGroup: "common",
  alertFilter: "active",
  mapDisease: null, // null = all diseases
  mapWeek: "current",
  selected: null, // barangay selected on the map
};
const data = { summary: null, grids: {}, alerts: [], nodes: [], reports: [] };
let config;
let tracked; // diseases under outbreak surveillance (config.diseases also has "Other consultations")
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
  tracked = config.diseases.filter((d) => d.tracked);
  state.disease = tracked[0].disease_code;
  setupMenu();
  setupLogout();
  buildControls();
  [overviewMap, bigMap] = await Promise.all([
    createNavalMap($("ov-map"), { labels: false, onSelect: (id) => selectBarangay(id, true) }),
    createNavalMap($("big-map"), { onSelect: (id) => selectBarangay(id, false) }),
  ]);
  watchStations();
  const accounts = createAccountsScreen();
  router = createRouter("overview", (screen) => {
    if (screen === "accounts") accounts.show();
    const [title, subtitle] = SCREENS[screen];
    $("screen-title").textContent = title;
    $("screen-subtitle").textContent = subtitle;
    document.title = `HealthPulz · ${title}`;
    if (data.summary) renderScreen(screen);
  });
  await refresh();
  renderReports(); // again, now that "this week" and "last week" are known
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
    const snapshot = JSON.parse(event.data);
    data.nodes = snapshot.nodes;
    renderStations();
    updateReports(snapshot.latest_reports);
  };
  ws.onclose = (event) => {
    if (event.code === api.CLOSE_NOT_LOGGED_IN) return api.toLogin();
    setTimeout(watchStations, 3000);
  };
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
  ({ overview: renderOverview, map: renderMapScreen, alerts: renderAlertsScreen, trends: renderTrends, barangays: renderGrid })[screen]?.();
}

// ---- Latest reports --------------------------------------------------------------------

const seenReports = new Set(); // `${record_id}.${revision}`, so only what just arrived flashes
let reportsKey = null;

/** The feed sends the list every second; redraw only when it changed. */
function updateReports(reports) {
  const key = reports.map(reportId).join();
  if (key === reportsKey) return;
  const isFirstDraw = reportsKey === null;
  reportsKey = key;
  data.reports = reports;
  renderReports({ flashNew: !isFirstDraw });
}

const reportId = (report) => `${report.record_id}.${report.revision}`;

function renderReports({ flashNew = false } = {}) {
  const rows = data.reports.map((report) => {
    const isNew = !seenReports.has(reportId(report));
    seenReports.add(reportId(report));
    return reportRow(report, flashNew && isNew);
  });
  $("latest-reports").replaceChildren(
    ...(rows.length ? rows : [el("li", { className: "empty", textContent: "No reports yet. They appear here the moment a BHU saves a checkup." })]),
  );
}

function reportRow(report, flash) {
  const barangay = config.barangays.find((b) => b.barangay_id === report.barangay_id)?.name ?? report.barangay_id;
  const disease = config.diseases.find((d) => d.disease_code === report.disease_code)?.name ?? report.disease_code;
  const change = report.voided ? "Voided" : report.revision ? "Corrected" : null;
  return el(
    "li",
    { className: `report-row${flash ? " is-new" : ""}${report.voided ? " is-voided" : ""}` },
    el("time", { className: "muted small", textContent: receivedTime(report.received_at) }),
    el("div", {}, el("strong", { textContent: disease }), el("div", { className: "muted small", textContent: `Brgy. ${barangay}` })),
    el("span", { className: "small", textContent: `${report.age_years} ${report.sex} · Purok ${report.purok}` }),
    el("span", { className: "badge", textContent: change ?? (report.case_status === "CONFIRMED" ? "Confirmed" : "Suspected"), dataset: change ? { level: "ALERT" } : {} }),
    el("span", { className: "muted small", textContent: `Illness started ${formatDate(report.onset_date)} · counts in ${weekName(report.counted_in_week)}` }),
  );
}

function receivedTime(timestamp) {
  const when = new Date(timestamp);
  const time = when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return when.toDateString() === new Date().toDateString() ? time : `${when.toLocaleDateString(undefined, { month: "short", day: "numeric" })}, ${time}`;
}

function weekName(label) {
  if (label === data.summary?.week.label) return "this week";
  if (label === data.summary?.previous_week.label) return "last week";
  return `week ${label}`;
}

// ---- Overview ------------------------------------------------------------------------

function renderOverview() {
  const { totals, diseases } = data.summary;
  const grid = data.grids.current.barangays;
  const active = activeAlerts();

  $("kpi-cases").textContent = fmt(totals.this_week);
  $("kpi-cases-sub").textContent = `Last week: ${fmt(totals.last_week)} · Other consultations: ${fmt(data.summary.other_consultations.this_week)}`;
  $("kpi-alerts").textContent = fmt(active.length);
  const atLevel = (level) => active.filter((a) => a.level === level).length;
  $("kpi-alerts-sub").textContent = active.length
    ? `${atLevel("URGENT")} urgent · ${atLevel("EPIDEMIC")} epidemic · ${active.filter((a) => a.status === "OPEN").length} not yet acknowledged`
    : "All barangays within normal levels";
  const withCases = grid.filter((b) => b.total > 0).length;
  const onAlert = grid.filter((b) => Object.values(b.diseases).some((c) => c.level !== "NORMAL")).length;
  $("kpi-barangays").textContent = `${withCases} / ${grid.length}`;
  $("kpi-barangays-sub").textContent = `${onAlert} over a threshold this week`;
  renderStations();

  overviewMap.update(data.grids[state.mapWeek].barangays);
  $("ov-map-hint").textContent = mapHint();
  const latest = active.slice(0, OVERVIEW_ALERTS);
  $("ov-alerts").replaceChildren(
    ...(latest.length ? latest.map((a) => alertRow(a, { compact: true })) : [el("p", { className: "empty", textContent: "No active alerts. All barangays are within normal levels." })]),
  );
  renderIllnesses(diseases);
}

function renderStations() {
  if (!$("kpi-stations")) return;
  const online = data.nodes.filter((n) => n.status === "ONLINE").length;
  $("kpi-stations").textContent = fmt(online);
  const barangays = new Set(data.nodes.map((n) => n.barangay_id)).size;
  $("kpi-stations-sub").textContent = `${fmt(data.nodes.length)} active in the last 12 hours · ${barangays} of 26 barangays`;
}

async function renderIllnesses(diseases) {
  // Most cases first; last week breaks ties, since early in a week most counts are low.
  const listed = diseases
    .filter((d) => d.group !== "urgent")
    .sort((a, b) => b.this_week - a.this_week || b.last_week - a.last_week);
  const trends = await Promise.all(listed.map((d) => api.fetchJson(api.trendUrl(d.disease_code, "", SPARKLINE_WEEKS))));
  $("common-list").replaceChildren(
    ...listed.map((d, i) => {
      const spark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      spark.setAttribute("aria-hidden", "true");
      lineChart(spark, [{ values: trends[i].weeks.map((w) => w.cases), className: "series-accent" }], { slots: SPARKLINE_WEEKS });
      return illnessRow(d, spark);
    }),
  );
  $("urgent-list").replaceChildren(...diseases.filter((d) => d.group === "urgent").map((d) => illnessRow(d)));
}

function illnessRow(d, spark = null) {
  const cases = d.this_week > 0 && d.group === "urgent";
  const row = el(
    "button",
    { type: "button", className: `rank-row${cases ? " urgent" : ""}`, title: `Open the ${d.name} trend` },
    el(
      "span",
      { className: "grow" },
      el("span", { className: "rank-name", textContent: d.name }),
      d.group === "monitored" ? el("span", { className: "chip", textContent: "Also monitored", style: "margin-left: 6px" }) : null,
      el("span", {
        className: "small muted",
        style: "display: block",
        textContent: `last week ${fmt(d.last_week)}${d.barangays_on_alert ? ` · ${d.barangays_on_alert} barangay${d.barangays_on_alert > 1 ? "s" : ""} on alert` : ""}`,
      }),
    ),
    spark,
    el("span", { className: "rank-value", textContent: fmt(d.this_week) }),
  );
  row.addEventListener("click", () => {
    state.disease = d.disease_code;
    router.go("trends");
  });
  return row;
}

// ---- Map -------------------------------------------------------------------------------

function renderMapScreen() {
  bigMap.update(data.grids[state.mapWeek].barangays, state.mapDisease);
  $("big-map-hint").textContent = mapHint();
  bigMap.select(state.selected);
  renderDetail();
}

/** Says which week the map shows; early in a week, or before real entries, it can be nearly empty. */
function mapHint() {
  const { week, previous_week: previous, totals } = data.summary;
  return state.mapWeek === "current"
    ? `Showing ${week.label}, still in progress: ${fmt(totals.this_week)} case(s) so far across all diseases. Last week (${previous.label}) had ${fmt(totals.last_week)}.`
    : `Showing last week, ${previous.label}: ${fmt(totals.last_week)} case(s) across all diseases.`;
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
  const worst = Object.values(b.diseases).reduce((w, c) => (LEVEL_RANK[c.level] > LEVEL_RANK[w] ? c.level : w), "NORMAL");

  const withCases = tracked.filter((d) => b.diseases[d.disease_code]?.cases > 0);
  const rows = withCases.map((d) => {
    const cell = b.diseases[d.disease_code];
    return el(
      "div",
      {},
      el("span", { textContent: d.name }),
      el("span", {}, cell.level !== "NORMAL" ? el("span", { className: "badge", dataset: { level: cell.level }, textContent: cell.level }) : "", ` ${fmt(cell.cases)}`),
    );
  });
  if (!rows.length) rows.push(el("div", { className: "muted", textContent: "No tracked illness this week." }));
  if (b.other_consultations) rows.push(el("div", { className: "muted" }, el("span", { textContent: "Other consultations" }), el("span", { textContent: fmt(b.other_consultations) })));
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
    // BHU corrections can lower the count after the alert was raised; staff decide if it still applies.
    alert.status !== "RESOLVED" && alert.current_count != null && alert.current_count < alert.observed
      ? el("div", { className: "note note-danger", textContent: `Corrected by the BHU: now ${alert.current_count} case(s), down from ${alert.observed}. Review whether this alert still applies.` })
      : null,
    el("div", {
      className: "muted small",
      textContent: compact
        ? `${alert.level === "URGENT" ? `${alert.observed} case${alert.observed === 1 ? "" : "s"}: any single case is urgent` : `${alert.observed} cases (threshold ${alert.threshold})`} · ${statusText}`
        : `${statusText} · raised ${new Date(alert.created_at).toLocaleString()} · sent to ${alert.nodes_delivered} station${alert.nodes_delivered === 1 ? "" : "s"}, ${alert.nodes_acknowledged} acknowledged`,
    }),
    actions,
  );
}

// ---- Trends ------------------------------------------------------------------------------

async function renderTrends() {
  $("trend-disease").value = state.disease;
  $("trend-barangay").value = state.barangay;

  const { weeks } = await api.fetchJson(api.trendUrl(state.disease, state.barangay, state.weeks));
  const selected = config.diseases.find((d) => d.disease_code === state.disease);
  const withThresholds = Boolean(state.barangay) && selected.threshold_alerts;
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
    : selected.single_case_alert
    ? "A single case of this disease raises an urgent alert, so it has no thresholds. The last week is still in progress."
    : !selected.threshold_alerts
    ? "Counted for trends. It isn't contagious, or isn't under outbreak surveillance, so it has no thresholds and raises no alerts. The last week is still in progress."
    : "Choose a barangay to compare with its alert and epidemic thresholds. The last week is still in progress.";
  const labels = weeks.length ? [weeks[0], weeks[Math.floor(weeks.length / 2)], weeks.at(-1)] : [];
  $("trend-axis").replaceChildren(...labels.map((w, i) => el("span", { textContent: i === 2 ? `${w.label} (so far)` : w.label })));
}

// ---- Barangays -----------------------------------------------------------------------------

function renderGrid() {
  const week = state.gridWeek === "current" ? data.summary.week : data.summary.previous_week;
  $("grid-title").textContent = `Barangays · ${week.label}${state.gridWeek === "current" ? " (so far)" : ""}`;
  const columns = tracked.filter((d) => d.group === state.gridGroup);
  $("grid-head").replaceChildren(
    el("th", { scope: "col", textContent: "Barangay" }),
    ...columns.map((d) => el("th", { scope: "col", textContent: d.name })),
    el("th", { scope: "col", textContent: "All tracked" }),
    el("th", { scope: "col", textContent: "Other" }),
    el("th", { scope: "col", textContent: "Per 10,000" }),
  );
  const rows = [...data.grids[state.gridWeek].barangays].sort((a, b) => b.rate_per_10k - a.rate_per_10k || a.name.localeCompare(b.name));
  $("grid-body").replaceChildren(
    ...rows.map((b) => {
      const row = el(
        "tr",
        { title: `Show Brgy. ${b.name} on the map`, tabIndex: 0 },
        el("th", { scope: "row", textContent: b.name }),
        ...columns.map((d) => {
          const cell = b.diseases[d.disease_code] ?? { cases: 0, level: "NORMAL" };
          return el("td", {
            textContent: fmt(cell.cases),
            className: cell.cases ? "" : "zero",
            dataset: { level: cell.level },
            title: d.single_case_alert ? "Any single case is urgent" : !d.threshold_alerts ? "Counted for trends; no outbreak alerts" : cell.alert_threshold == null ? "No threshold yet" : `Alert above ${cell.alert_threshold}, epidemic above ${cell.epidemic_threshold}`,
          });
        }),
        el("td", { textContent: fmt(b.total) }),
        el("td", { textContent: fmt(b.other_consultations), className: b.other_consultations ? "" : "zero" }),
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
  $("trend-disease").append(...diseaseOptions(tracked), el("option", { value: "OTH", textContent: "Other consultations" }));
  $("trend-disease").addEventListener("change", (e) => {
    state.disease = e.target.value;
    renderTrends();
  });
  $("map-disease").append(...diseaseOptions(tracked));
  $("map-disease").addEventListener("change", (e) => {
    state.mapDisease = e.target.value || null;
    renderMapScreen();
  });
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
  pressGroup("[data-grid-group]", "gridGroup", renderGrid);
  pressGroup("[data-map-week]", "mapWeek", () => renderScreen(router.current())); // on both Overview and Map
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
