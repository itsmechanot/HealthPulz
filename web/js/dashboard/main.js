// RHU Dashboard: redraws when the analytics finish a run.

import * as api from "../common/api.js";
import { lineChart } from "../common/chart.js";
import { weekStart } from "../common/dates.js";
import { diseaseOptions } from "../common/diseases.js";
import { el } from "../common/dom.js";
import { applyPlace, fillPlace } from "../common/place.js";
import { createTownMap } from "../common/town-map.js";
import { confirmAction, createRouter, setupLogout, setupMenu } from "../common/shell.js";
import { createAccountsScreen } from "./accounts.js";
import { createDayPicker, toIso } from "./calendar.js";

const REFRESH_MS = 10_000;
const SPARKLINE_WEEKS = 12;
const OVERVIEW_ALERTS = 4;
const LEVEL_RANK = { NORMAL: 0, ALERT: 1, EPIDEMIC: 2, URGENT: 3 };
const SCREENS = {
  overview: ["Surveillance overview", "Weekly disease reports across {town}, {province}"],
  map: ["Geographic view", "Reported cases by barangay in {town}"],
  alerts: ["Alerts", "Outbreaks detected by the parallel analytics, by severity"],
  trends: ["Case trends", "Weekly reports compared with outbreak thresholds"],
  barangays: ["Barangay summary", "Reported disease counts across all barangays"],
  accounts: ["Health unit access", "Give each Barangay Health Unit its password"],
};

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString();

const state = {
  disease: null, // Trends screen
  barangay: "", // Trends screen: "" = the whole municipality
  weeks: 26,
  gridWeek: "current",
  gridGroup: "common",
  gridSort: "attention", // Barangays screen: "attention", a disease code, "total", "other" or "rate"
  gridOnlyCases: true,
  alertFilter: "active",
  alertLevel: "ALERT", // Alerts screen: one severity tab at a time
  mapDisease: null, // null = all diseases
  mapWeek: "current",
  mapDay: null, // "YYYY-MM-DD" when the map's calendar shows one day; null = the week toggle
  selected: null, // barangay selected on the map
};
const data = { summary: null, grids: {}, dayGrid: null, alerts: [], nodes: [], reports: [] };
let config;
let tracked;  // outbreak-tracked diseases (not "Other")
let router;
let overviewMap;
let bigMap;
let dayPickers = [];

start();

async function start() {
  try {
    config = await api.getConfig();
    applyPlace(config.municipality);
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
    createTownMap($("ov-map"), { labels: true, onSelect: (id) => selectBarangay(id, true) }),
    createTownMap($("big-map"), { onSelect: (id) => selectBarangay(id, false) }),
  ]);
  dayPickers = [overviewMap, bigMap].map((map) =>
    createDayPicker(map.corner, {
      getDay: () => state.mapDay,
      onPick: pickDay,
      loadMonth: (month) => api.fetchJson(`/api/stats/days?month=${month}`),
    }),
  );
  watchStations();
  const accounts = createAccountsScreen();
  router = createRouter("overview", (screen) => {
    if (screen === "accounts") accounts.show();
    const [title, subtitle] = SCREENS[screen];
    $("screen-title").textContent = title;
    $("screen-subtitle").textContent = fillPlace(subtitle);
    document.title = `HealthPulz · ${title}`;
    if (data.summary) renderScreen(screen);
  });
  await refresh();
  renderReports(); // again, now that "this week" and "last week" are known
  router.start();
  setInterval(refresh, REFRESH_MS);
}

let refreshing = false;
let refreshAgain = false;

/** One at a time, so a slower, older answer can't be drawn over a newer one. */
async function refresh() {
  if (refreshing) {
    refreshAgain = true;
    return;
  }
  refreshing = true;
  try {
    data.summary = await api.fetchJson("/api/stats/summary");
    const [current, previous, alerts] = await Promise.all([
      api.fetchJson(`/api/stats/barangays?week=${data.summary.week.label}`),
      api.fetchJson(`/api/stats/barangays?week=${data.summary.previous_week.label}`),
      api.fetchJson("/api/alerts"),
    ]);
    data.grids = { current, previous };
    if (state.mapDay) data.dayGrid = await api.fetchJson(dayUrl(state.mapDay)); // a day of this week keeps updating
    data.alerts = alerts;
    renderChrome();
    if (router) renderScreen(router.current());
    setConnection("online", "Live");
  } catch (error) {
    setConnection("offline", "Reconnecting…");
    console.warn("Dashboard refresh failed", error);
  } finally {
    refreshing = false;
    if (refreshAgain) {
      refreshAgain = false;
      refresh();
    }
  }
}

/** Stations online come from the live monitor feed (one snapshot per second). */
function watchStations() {
  const ws = new WebSocket(api.wsUrl("/ws/monitor"));
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "ANALYTICS_DONE") {
      refreshAfterAnalytics(message.finished_at);
      return;
    }
    data.nodes = message.nodes;
    renderStations();
    updateReports(message.latest_reports);
    refreshAfterAnalytics(message.analytics?.finished_at); // in case the message above was missed
  };
  ws.onclose = (event) => {
    if (event.code === api.CLOSE_NOT_LOGGED_IN) return api.toLogin();
    setTimeout(watchStations, 3000);
  };
}

let lastAnalyticsRun = null;

/** New numbers are ready as soon as a run finishes, so show them right away. */
function refreshAfterAnalytics(finishedAt) {
  if (!finishedAt) return;
  if (lastAnalyticsRun && finishedAt !== lastAnalyticsRun) refresh();
  lastAnalyticsRun = finishedAt;
}

function setConnection(stateName, text) {
  $("connection").dataset.state = stateName;
  $("connection").textContent = text;
  $("dashboard-guidance").hidden = stateName !== "offline";
  $("dashboard-guidance").textContent = "The RHU server cannot be reached. Dashboard figures may be out of date; it will retry automatically.";
}

const activeAlerts = () => data.alerts.filter((a) => a.status !== "RESOLVED");

function renderChrome() {
  const { week } = data.summary;
  $("week-chip-dates").textContent = week.dates;
  const open = activeAlerts().filter((a) => a.status === "OPEN").length;
  $("nav-alert-count").hidden = open === 0;
  $("nav-alert-count").textContent = open;
}

function renderScreen(screen) {
  ({ overview: renderOverview, map: renderMapScreen, alerts: renderAlertsScreen, trends: renderTrends, barangays: renderGrid })[screen]?.();
}

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
    ...(rows.length ? rows : [el("li", { className: "empty", textContent: "No reports yet. They appear here the moment a BHU saves a consultation." })]),
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
    el("span", { className: "muted small", textContent: `Consultation ${formatDate(report.consult_date)} · symptom onset ${formatDate(report.onset_date)} · counts in ${weekName(report)}` }),
  );
}

function receivedTime(timestamp) {
  const when = new Date(timestamp);
  const time = when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return when.toDateString() === new Date().toDateString() ? time : `${when.toLocaleDateString(undefined, { month: "short", day: "numeric" })}, ${time}`;
}

function weekName(report) {
  if (report.counted_in_week === data.summary?.week.label) return "this week";
  if (report.counted_in_week === data.summary?.previous_week.label) return "last week";
  return `the week of ${report.counted_week_dates}`;
}

let commonListDrawing = 0;

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
    : "No reported counts currently exceed outbreak thresholds";
  const withCases = grid.filter((b) => b.total > 0).length;
  const onAlert = grid.filter((b) => Object.values(b.diseases).some((c) => c.level !== "NORMAL")).length;
  $("kpi-barangays").textContent = `${withCases} / ${grid.length}`;
  $("kpi-barangays-sub").textContent = `${onAlert} over a threshold this week`;
  renderStations();

  overviewMap.update(mapGrid());
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
  const drawing = ++commonListDrawing;
  const trends = await Promise.all(listed.map((d) => api.fetchJson(api.trendUrl(d.disease_code, "", SPARKLINE_WEEKS))));
  if (drawing !== commonListDrawing) return; // a newer refresh started while these were loading
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

function renderMapScreen() {
  bigMap.update(mapGrid(), state.mapDisease);
  $("big-map-hint").textContent = mapHint();
  bigMap.select(state.selected);
  renderDetail();
}

const dayUrl = (day) => `/api/stats/day?date=${day}`;

/** What both maps show: the chosen day, or the week from the This week / Last week toggle. */
const mapGrid = () => (state.mapDay && data.dayGrid ? data.dayGrid.barangays : data.grids[state.mapWeek].barangays);

let picking = 0;

/** day: "YYYY-MM-DD", or null to go back to the week. Both maps follow. */
async function pickDay(day) {
  const token = ++picking;
  const grid = day ? await api.fetchJson(dayUrl(day)).catch(() => null) : null;
  if (token !== picking || (day && !grid)) return;
  state.mapDay = day;
  data.dayGrid = grid;
  setPressed("[data-map-week]", day ? null : state.mapWeek);
  dayPickers.forEach((picker) => {
    picker.refresh();
    picker.close();
  });
  renderScreen(router.current());
}

function clearDay() {
  if (!state.mapDay) return;
  state.mapDay = null;
  data.dayGrid = null;
  dayPickers.forEach((picker) => picker.refresh());
}

/** Which week or day the map shows. */
function mapHint() {
  if (state.mapDay && data.dayGrid) {
    const g = data.dayGrid;
    const withCases = g.barangays.filter((b) => b.total > 0).length;
    const flagged = g.barangays.filter((b) => Object.values(b.diseases).some((c) => c.week_level)).length;
    const today = g.date === toIso(new Date()) ? " (today, so far)" : "";
    return `Showing one day, ${g.dates}${today}: ${fmt(g.total)} case(s) in ${withCases} barangay(s), by checkup date.` +
      (flagged ? ` A colored circle marks ${flagged} barangay(s) with an outbreak alert that week.` : "");
  }
  const { week, previous_week: previous, totals } = data.summary;
  return state.mapWeek === "current"
    ? `Showing this week, ${week.dates}, still in progress: ${fmt(totals.this_week)} case(s) so far across all diseases. Last week (${previous.dates}) had ${fmt(totals.last_week)}.`
    : `Showing last week, ${previous.dates}: ${fmt(totals.last_week)} case(s) across all diseases.`;
}

let scrollToDetail = false; // set by a click on a barangay; the 10-second refresh never scrolls

function selectBarangay(id, fromOverview) {
  state.selected = id;
  scrollToDetail = true;
  if (fromOverview) router.go("map"); // the Map screen renders, then scrolls to the details
  else {
    bigMap.select(id);
    renderDetail();
  }
}

/** After a click on the map: bring the barangay's details into view. */
function revealDetail() {
  if (!scrollToDetail) return;
  scrollToDetail = false;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  requestAnimationFrame(() => $("detail-title").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" }));
}

function renderDetail() {
  if (!state.selected) return;
  const b = mapGrid().find((x) => x.barangay_id === state.selected);
  const meta = config.barangays.find((x) => x.barangay_id === state.selected);
  const week = state.mapWeek === "current" ? `${data.summary.week.dates} (so far)` : data.summary.previous_week.dates;
  const period = state.mapDay && data.dayGrid ? data.dayGrid.dates : `Week ${week}`;
  const levelOf = (c) => (c.level !== "NORMAL" ? c.level : c.week_level ?? "NORMAL"); // a day shows its week's level
  const worst = Object.values(b.diseases).reduce((w, c) => (LEVEL_RANK[levelOf(c)] > LEVEL_RANK[w] ? levelOf(c) : w), "NORMAL");

  const withCases = tracked.filter((d) => b.diseases[d.disease_code]?.cases > 0);
  const rows = withCases.map((d) => {
    const cell = b.diseases[d.disease_code];
    return el(
      "div",
      {},
      el("span", { textContent: d.name }),
      el("span", {}, levelOf(cell) !== "NORMAL" ? el("span", { className: "badge", dataset: { level: levelOf(cell) }, textContent: levelOf(cell) }) : "", ` ${fmt(cell.cases)}`),
    );
  });
  if (!rows.length) rows.push(el("div", { className: "muted", textContent: state.mapDay ? "No tracked illness this day." : "No tracked illness this week." }));
  if (b.other_consultations) rows.push(el("div", { className: "muted" }, el("span", { textContent: "Other consultations" }), el("span", { textContent: fmt(b.other_consultations) })));
  const trendBtn = el("button", { type: "button", className: "btn btn-ghost btn-small", textContent: "Open trend" });
  trendBtn.addEventListener("click", () => {
    state.barangay = b.barangay_id;
    $("trend-barangay").value = b.barangay_id;
    router.go("trends");
  });

  $("map-detail").replaceChildren(
    el("div", { className: "station-header map-station-header" },
      el("span", { className: "avatar", textContent: initials(b.name) }),
      el("div", { className: "grow" },
        el("div", { className: "map-station-name" },
          el("h3", { textContent: `Brgy. ${b.name}` }),
          el("span", { className: "badge map-status-badge", dataset: { level: worst === "NORMAL" ? "OK" : worst }, textContent: worst === "NORMAL" ? "Normal" : worst }),
        ),
        el("span", { className: "muted small", textContent: period }),
      ),
    ),
    el("div", { className: "stats three" },
      el("div", { className: "stat" }, el("div", { className: "stat-value", textContent: fmt(b.total) }), el("div", { className: "stat-label", textContent: "Reported cases" })),
      el("div", { className: "stat" }, el("div", { className: "stat-value", textContent: b.rate_per_10k.toFixed(1) }), el("div", { className: "stat-label", textContent: "Per 10,000" })),
      el("div", { className: "stat" }, el("div", { className: "stat-value", textContent: fmt(b.population) }), el("div", { className: "stat-label", textContent: "Population*" })),
    ),
    el("div", { className: "detail-list" }, ...rows),
    el("p", { className: "muted small", style: "margin: 0", textContent: `Neighbors: ${meta.neighbors.map(barangayName).join(", ") || "none (island)"}. They also receive this barangay's alerts. *Synthetic population.` }),
    trendBtn,
  );
  revealDetail();
}

function renderAlertsScreen() {
  const byStatus = data.alerts.filter((a) =>
    state.alertFilter === "active" ? a.status !== "RESOLVED" : state.alertFilter === "RESOLVED" ? a.status === "RESOLVED" : true);
  for (const level of ["ALERT", "EPIDEMIC", "URGENT"]) {
    const count = byStatus.filter((a) => a.level === level).length;
    const unacknowledged = data.alerts.some((a) => a.level === level && a.status === "OPEN");
    $(`alert-count-${level}`).textContent = fmt(count);
    $(`alert-new-${level}`).hidden = !unacknowledged;
    document.querySelector(`[data-alert-level="${level}"]`)
      .setAttribute("aria-label", `${level[0]}${level.slice(1).toLowerCase()}: ${count} alert${count === 1 ? "" : "s"}${unacknowledged ? ", new unacknowledged alert" : ""}`);
  }
  const alerts = byStatus.filter((a) => a.level === state.alertLevel);
  const levelName = state.alertLevel.toLowerCase();
  const empty = state.alertFilter === "active" ? `No active ${levelName} alerts.` : `No ${levelName} alerts to display.`;
  $("alerts").replaceChildren(...(alerts.length ? alerts.map((a) => alertRow(a)) : [el("p", { className: "empty", textContent: empty })]));
}

function alertRow(alert, { compact = false } = {}) {
  const where = alert.barangay_name ?? barangayName(alert.barangay_id);
  const actions = el("div", { className: "actions" });
  const action = (label, status, style) => {
    const button = el("button", { type: "button", className: `btn btn-small alert-action-${style}`, textContent: label });
    const verb = { ACKNOWLEDGED: "acknowledge", RESOLVED: "resolve", OPEN: "reopen" }[status];
    button.addEventListener("click", () =>
      confirmAction({
        title: `${label} alert?`,
        message: `Are you sure you want to ${verb} the ${diseaseName(alert.disease_code)} alert for ${where}?`,
        type: status === "RESOLVED" ? "warning" : "info",
        confirmLabel: label,
        successTitle: `Alert ${verb === "reopen" ? "reopened" : `${verb}d`}`,
        successMessage: `${diseaseName(alert.disease_code)} · ${where}`,
        action: async () => {
          await api.fetchJson(`/api/alerts/${alert.alert_id}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status }),
          });
          await refresh();
        },
      }),
    );
    return button;
  };
  if (alert.status === "OPEN") actions.append(action("Acknowledge", "ACKNOWLEDGED", "acknowledge"));
  if (alert.status !== "RESOLVED") actions.append(action("Resolve", "RESOLVED", "resolve"));
  else actions.append(action("Reopen", "OPEN", "reopen"));
  const showTrend = el("button", { type: "button", className: "btn btn-small alert-action-trend", textContent: "View case trend" });
  showTrend.addEventListener("click", () => {
    state.disease = alert.disease_code;
    state.barangay = alert.barangay_id;
    $("trend-barangay").value = alert.barangay_id;
    router.go("trends");
  });
  actions.append(showTrend);

  const statusText = { OPEN: "Open", ACKNOWLEDGED: "Acknowledged by RHU", RESOLVED: "Resolved" }[alert.status];
  const levelName = alert.level === "EPIDEMIC" ? "epidemic" : "alert";
  const contextItem = (label, value, note = "") =>
    el("div", { className: "alert-context-item" },
      el("span", { className: "alert-context-label", textContent: label }),
      el("strong", { className: "alert-context-value", textContent: value }),
      note ? el("span", { className: "alert-context-note", textContent: note }) : null,
    );
  const statusDetails = compact
    ? el("div", { className: "alert-meta", textContent: `${alert.level === "URGENT" ? `${alert.observed} case${alert.observed === 1 ? "" : "s"}: any single case is urgent` : `${alert.observed} cases (threshold ${alert.threshold})`} · ${statusText}` })
    : el("div", { className: "alert-meta" },
      el("span", { className: "badge alert-status-badge", dataset: { status: alert.status }, textContent: statusText }),
      el("span", { className: "muted small", textContent: `Raised: ${new Date(alert.created_at).toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })}` }),
      el("span", { className: "muted small", textContent: `Delivered to ${alert.nodes_delivered} health station${alert.nodes_delivered === 1 ? "" : "s"} · Acknowledged by ${alert.nodes_acknowledged} station${alert.nodes_acknowledged === 1 ? "" : "s"}` }),
    );
  return el(
    "article",
    { className: "alert-row", dataset: { level: alert.level, status: alert.status } },
    el("div", { className: "alert-row-heading" },
      el("span", { className: "badge", dataset: { level: alert.level }, textContent: alert.level }),
      el("strong", { textContent: `${diseaseName(alert.disease_code)} · Barangay ${where}` }),
      compact ? el("span", { className: "muted small", textContent: alert.week_dates }) : null,
    ),
    compact ? null : el("div", {
      className: "alert-message",
      textContent: alert.level === "URGENT"
        ? `A single case of ${diseaseName(alert.disease_code)} needs immediate investigation.`
        : `The number of ${diseaseName(alert.disease_code)} cases is above the ${levelName} threshold.`,
    }),
    compact ? null : el("div", { className: "alert-context" },
      contextItem("Reported cases", String(alert.observed)),
      alert.level === "URGENT"
        ? contextItem("Urgent rule", "One case is enough")
        : contextItem(`${levelName[0].toUpperCase()}${levelName.slice(1)} threshold`, `${Number(alert.threshold).toFixed(2)} cases`, "Calculated from past reports"),
      contextItem("Case reporting period", alert.week_dates),
    ),
    // BHU corrections can lower the count; staff decide.
    alert.status !== "RESOLVED" && alert.current_count != null && alert.current_count < alert.observed
      ? el("div", {
        className: "note note-danger alert-correction",
        textContent: `The BHU updated its reports. The count is now ${alert.current_count} case${alert.current_count === 1 ? "" : "s"}, down from ${alert.observed}. Please review whether this alert is still needed.`,
      })
      : null,
    el("div", { className: "alert-row-footer" }, statusDetails, actions),
  );
}

let trendRequest = 0;

async function renderTrends() {
  $("trend-disease").value = state.disease;
  $("trend-barangay").value = state.barangay;

  // Filters can change faster than the server answers: only the latest answer is drawn.
  const request = ++trendRequest;
  const { weeks } = await api.fetchJson(api.trendUrl(state.disease, state.barangay, state.weeks));
  if (request !== trendRequest) return;
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
  const area = state.barangay ? `Brgy. ${barangayName(state.barangay)}` : fillPlace("all of {town}");
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
  $("trend-axis").replaceChildren(...labels.map((w, i) => el("span", { textContent: i === 2 ? `${weekStart(w)} (so far)` : weekStart(w) })));
}

const GRID_LEVEL_RANK = { NORMAL: 0, ALERT: 1, EPIDEMIC: 2, URGENT: 3 };

function renderGrid() {
  const week = state.gridWeek === "current" ? data.summary.week : data.summary.previous_week;
  $("grid-title").textContent = `Barangays · ${week.dates}${state.gridWeek === "current" ? " (so far)" : ""}`;
  const columns = tracked.filter((d) => d.group === state.gridGroup);
  const all = data.grids[state.gridWeek].barangays;
  const cellOf = (b, code) => b.diseases[code] ?? { cases: 0, level: "NORMAL" };
  const worst = (b, codes) => codes.reduce((w, code) => Math.max(w, GRID_LEVEL_RANK[cellOf(b, code).level] ?? 0), 0);
  const shownCases = (b) => columns.reduce((sum, d) => sum + cellOf(b, d.disease_code).cases, 0);

  // Headline: where something is happening this week.
  const allCodes = tracked.map((d) => d.disease_code);
  const flagged = all.filter((b) => worst(b, allCodes) > 0).length;
  const byDisease = tracked.map((d) => [d, all.reduce((sum, b) => sum + cellOf(b, d.disease_code).cases, 0)]).sort((a, b) => b[1] - a[1]);
  const busiest = [...all].sort((a, b) => b.total - a.total)[0];
  $("grid-summary").replaceChildren(...(busiest?.total
    ? [
      el("strong", { textContent: `${flagged} barangay${flagged === 1 ? "" : "s"}` }), ` over a threshold or with an urgent case · most reported: `,
      el("strong", { textContent: `${byDisease[0][0].name} (${fmt(byDisease[0][1])})` }), " · busiest: ",
      el("strong", { textContent: `${busiest.name} (${fmt(busiest.total)} case${busiest.total === 1 ? "" : "s"})` }),
    ]
    : ["No tracked cases reported yet for this week."]));

  // "attention": over a threshold first, then most cases.
  const sortValue = {
    total: (b) => b.total, other: (b) => b.other_consultations, rate: (b) => b.rate_per_10k,
    ...Object.fromEntries(columns.map((d) => [d.disease_code, (b) => cellOf(b, d.disease_code).cases])),
  };
  if (state.gridSort !== "attention" && !sortValue[state.gridSort]) state.gridSort = "attention";
  const byAttention = (a, b) => worst(b, columns.map((d) => d.disease_code)) - worst(a, columns.map((d) => d.disease_code))
    || shownCases(b) - shownCases(a) || b.total - a.total || a.name.localeCompare(b.name);
  const rows = [...all].sort(state.gridSort === "attention" ? byAttention
    : (a, b) => sortValue[state.gridSort](b) - sortValue[state.gridSort](a) || byAttention(a, b));
  const visible = state.gridOnlyCases ? rows.filter((b) => shownCases(b) > 0 || worst(b, columns.map((d) => d.disease_code)) > 0) : rows;

  const heading = (key, label, title = label) => {
    const active = state.gridSort === key;
    return el("th", { scope: "col", "aria-sort": active ? "descending" : "none" },
      el("button", {
        type: "button", className: `grid-sort${active ? " active" : ""}`, title: active ? `${title}: sorted, highest first. Click to sort by attention` : `Sort by ${title}`,
        onclick: () => { state.gridSort = active ? "attention" : key; renderGrid(); },
      }, label, el("span", { className: "grid-sort-mark", "aria-hidden": "true", textContent: active ? " ▼" : "" })));
  };
  $("grid-head").replaceChildren(
    el("th", { scope: "col" }, el("button", {
      type: "button", className: `grid-sort${state.gridSort === "attention" ? " active" : ""}`, title: "Sort by attention: over a threshold first, then the most cases",
      onclick: () => { state.gridSort = "attention"; renderGrid(); },
    }, "Barangay", el("span", { className: "grid-sort-mark", "aria-hidden": "true", textContent: state.gridSort === "attention" ? " ▼" : "" }))),
    ...columns.map((d) => heading(d.disease_code, d.name)),
    heading("total", "Total", "total tracked cases"),
    heading("other", "Other", "other consultations"),
    heading("rate", "Per 10,000", "cases per 10,000 people"),
  );

  // Heat: darker tint = more cases (relative to the busiest cell).
  const peak = Math.max(1, ...visible.flatMap((b) => columns.map((d) => cellOf(b, d.disease_code).cases)));
  const count = (n, extra = {}) => n
    ? el("td", { textContent: fmt(n), ...extra })
    : el("td", { className: "zero", ...extra }, el("span", { className: "sr-only", textContent: "0" }), el("span", { "aria-hidden": "true", textContent: "·" }));

  const hidden = rows.length - visible.length;
  $("grid-body").replaceChildren(
    ...visible.map((b) => {
      const row = el(
        "tr",
        { title: `Show Brgy. ${b.name} on the map`, tabIndex: 0 },
        el("th", { scope: "row", textContent: b.name }),
        ...columns.map((d) => {
          const cell = cellOf(b, d.disease_code);
          const td = count(cell.cases, {
            dataset: { level: cell.level },
            title: d.single_case_alert ? "Any single case is urgent" : !d.threshold_alerts ? "Counted for trends; no outbreak alerts" : cell.alert_threshold == null ? "No threshold yet" : `Alert above ${cell.alert_threshold}, epidemic above ${cell.epidemic_threshold}`,
          });
          if (cell.cases && cell.level === "NORMAL") {
            td.classList.add("heat-cell");
            td.style.setProperty("--heat", (0.12 + 0.5 * (cell.cases / peak)).toFixed(2));
          }
          return td;
        }),
        el("td", { className: "grid-total", textContent: fmt(b.total) }),
        count(b.other_consultations),
        el("td", { textContent: b.rate_per_10k.toFixed(1) }),
      );
      const open = () => {
        state.selected = b.barangay_id;
        state.mapWeek = state.gridWeek;
        clearDay();
        setPressed("[data-map-week]", state.mapWeek, "mapWeek");
        router.go("map");
      };
      row.addEventListener("click", open);
      row.addEventListener("keydown", (e) => e.key === "Enter" && open());
      return row;
    }),
    ...(visible.length ? [] : [el("tr", {}, el("td", { colSpan: columns.length + 4, className: "grid-empty", textContent: "No barangay has a case of these diseases this week." }))]),
  );
  // Outside the table, so it doesn't scroll away on phones.
  $("grid-hidden").hidden = !hidden;
  $("grid-hidden").replaceChildren(...(hidden ? [
    `${hidden} barangay${hidden === 1 ? "" : "s"} with no cases of these diseases ${hidden === 1 ? "is" : "are"} hidden. `,
    el("button", { type: "button", className: "link-btn small", textContent: "Show all", onclick: () => { $("grid-only-cases").checked = false; state.gridOnlyCases = false; renderGrid(); } }),
  ] : []));
}

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
  pressGroup("[data-alert-level]", "alertLevel", renderAlertsScreen);
  pressGroup("[data-grid-week]", "gridWeek", renderGrid);
  pressGroup("[data-grid-group]", "gridGroup", renderGrid);
  $("grid-only-cases").addEventListener("change", (e) => {
    state.gridOnlyCases = e.target.checked;
    renderGrid();
  });
  pressGroup("[data-map-week]", "mapWeek", () => {  // both maps; a week replaces a chosen day
    clearDay();
    renderScreen(router.current());
  });
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
