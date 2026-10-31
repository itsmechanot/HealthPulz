// Home page: live numbers from the RHU (one monitor snapshot + the stats summary).

import * as api from "../common/api.js";

const REFRESH_MS = 10_000;
const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString();

refresh();
setInterval(refresh, REFRESH_MS);

async function refresh() {
  try {
    const [summary, snapshot] = await Promise.all([api.fetchJson("/api/stats/summary"), monitorSnapshot()]);
    $("home-nodes").textContent = fmt(snapshot.nodes.filter((n) => n.status === "ONLINE").length);
    $("home-cases").textContent = fmt(summary.totals.this_week);
    $("home-alerts").textContent = fmt(summary.open_alerts);
    const run = snapshot.analytics;
    $("home-analytics").textContent = run ? `${run.total_seconds.toFixed(2)} s` : "–";
    $("home-analytics-label").textContent = run
      ? `Last analytics run · ${fmt(run.cases)} cases, ${run.workers} worker processes`
      : "Last analytics run";
    setConnection("online", "RHU online");
  } catch {
    setConnection("offline", "RHU unreachable");
  }
}

/** The monitor feed sends a full snapshot right after connecting; take it and close. */
function monitorSnapshot() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(api.wsUrl("/ws/monitor"));
    ws.onmessage = (event) => {
      resolve(JSON.parse(event.data));
      ws.close();
    };
    ws.onerror = () => reject(new Error("monitor feed unavailable"));
  });
}

function setConnection(state, text) {
  $("connection").dataset.state = state;
  $("connection").textContent = text;
}
