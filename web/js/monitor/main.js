// System Monitor: renders the RHU's live MONITOR feed (one snapshot per second over WebSocket).

import * as api from "../common/api.js";
import { lineChart } from "../common/chart.js";

const HISTORY_SECONDS = 120;
const RECONNECT_MS = 2000;

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString();
const history = { rate: [], ingest: [], write: [] };
let peakRate = 0;
let barangayNames = {};

start();

async function start() {
  try {
    const config = await api.getConfig();
    barangayNames = Object.fromEntries(config.barangays.map((b) => [b.barangay_id, b.name]));
  } catch {
    // Names are a nicety; tiles fall back to barangay IDs.
  }
  showJoinAddresses();
  connect();
}

async function showJoinAddresses() {
  try {
    const res = await fetch("/api/server/addresses");
    const { addresses, port } = await res.json();
    const suffix = port ? `:${port}` : "";
    $("join-urls").textContent = addresses.length
      ? addresses.map((ip) => `http://${ip}${suffix}/node/`).join("  or  ")
      : `http://${location.host}/node/`;
  } catch {
    $("join-urls").textContent = `http://${location.host}/node/`;
  }
}

function connect() {
  setConnection("connecting");
  const ws = new WebSocket(api.wsUrl("/ws/monitor"));
  ws.onopen = () => setConnection("online");
  ws.onmessage = (event) => render(JSON.parse(event.data));
  ws.onclose = () => {
    setConnection("offline");
    setTimeout(connect, RECONNECT_MS);
  };
}

function setConnection(state) {
  const labels = { online: "Live", connecting: "Connecting…", offline: "Disconnected, retrying…" };
  $("connection").dataset.state = state;
  $("connection").textContent = labels[state];
}

function render(snapshot) {
  const p = snapshot.pipeline;
  remember(p);
  renderKpis(p, snapshot.nodes);
  renderCharts();
  renderPipeline(p);
  renderAnalytics(snapshot.analytics);
  renderNodes(snapshot.nodes);
}

function remember(p) {
  const push = (list, value) => {
    list.push(value);
    if (list.length > HISTORY_SECONDS) list.shift();
  };
  push(history.rate, p.records_per_second);
  push(history.ingest, p.ingest_queue);
  push(history.write, p.write_queue);
  peakRate = Math.max(peakRate, p.records_per_second);
}

function renderKpis(p, nodes) {
  const online = nodes.filter((n) => n.status === "ONLINE").length;
  const accounted = p.records_accepted + p.records_duplicate + p.records_rejected;
  const inPipeline = p.records_received - accounted;

  $("kpi-rate").textContent = fmt(Math.round(p.records_per_second));
  $("kpi-peak").textContent = `Peak ${fmt(Math.round(peakRate))}`;
  $("kpi-accepted").textContent = fmt(p.records_accepted);
  $("kpi-batches").textContent = `${fmt(p.batches_stored)} batches`;
  $("kpi-nodes").textContent = fmt(online);
  $("kpi-nodes-total").textContent = `of ${fmt(nodes.length)} registered`;
  $("kpi-queue").textContent = fmt(p.ingest_queue + p.write_queue);
  $("kpi-busy").textContent = `${fmt(p.batches_busy)} refused (busy)`;
  $("kpi-accounted").textContent = p.records_received ? `${((accounted / p.records_received) * 100).toFixed(1)}%` : "–";
  $("kpi-accounted-sub").textContent = inPipeline > 0
    ? `${fmt(inPipeline)} still in the pipeline`
    : `${fmt(accounted)} of ${fmt(p.records_received)} · nothing lost`;
  // The demo's proof: once everything is through, highlight 100% in green.
  $("kpi-accounted-tile").classList.toggle("highlight", p.records_received > 0 && inPipeline === 0);
}

function renderCharts() {
  const rateMax = lineChart($("chart-throughput"), [{ values: history.rate, className: "series-accent" }], { slots: HISTORY_SECONDS });
  $("scale-throughput").textContent = `max ${fmt(Math.round(rateMax))}/s`;
  const queueMax = lineChart(
    $("chart-queues"),
    [
      { values: history.write, className: "series-muted" },
      { values: history.ingest, className: "series-warn" },
    ],
    { slots: HISTORY_SECONDS },
  );
  $("scale-queues").textContent = `max ${fmt(Math.round(queueMax))} batches`;
}

function renderPipeline(p) {
  $("st-received").textContent = fmt(p.batches_received);
  $("st-ingest").textContent = fmt(p.ingest_queue);
  $("st-capacity").textContent = `of ${fmt(p.queue_capacity)} (full → 503)`;
  $("st-validators").textContent = fmt(p.validator_threads);
  $("st-rejected").textContent = `threads · ${fmt(p.records_rejected)} records rejected`;
  $("st-write").textContent = fmt(p.write_queue);
  $("st-stored").textContent = fmt(p.batches_stored);
  $("st-duplicates").textContent = `batches stored · ${fmt(p.records_duplicate)} duplicates`;
  setMeter($("meter-ingest"), p.ingest_queue, p.queue_capacity);
  setMeter($("meter-write"), p.write_queue, p.queue_capacity);
}

function setMeter(meter, value, capacity) {
  const ratio = capacity ? value / capacity : 0;
  meter.firstElementChild.style.width = `${Math.min(100, ratio * 100)}%`;
  meter.dataset.level = ratio > 0.8 ? "danger" : ratio > 0.5 ? "warn" : "";
}

function renderAnalytics(run) {
  if (!run) return;
  const rows = [
    ["Worker processes", fmt(run.workers)],
    ["Partitions (barangays)", fmt(run.partitions)],
    ["Cases analyzed", fmt(run.cases)],
    ["Map (parallel)", `${run.map_seconds.toFixed(2)} s`],
    ["Reduce (write)", `${run.reduce_seconds.toFixed(2)} s`],
    ["Finished", new Date(run.finished_at).toLocaleTimeString()],
  ];
  $("analytics").replaceChildren(
    ...rows.flatMap(([label, value]) => [
      Object.assign(document.createElement("dt"), { textContent: label }),
      Object.assign(document.createElement("dd"), { textContent: value }),
    ]),
  );
}

function renderNodes(nodes) {
  const sorted = [...nodes].sort(
    (a, b) => (a.status === "ONLINE" ? 0 : 1) - (b.status === "ONLINE" ? 0 : 1) || name(a).localeCompare(name(b)),
  );
  $("nodes").replaceChildren(
    ...sorted.map((node) => {
      const tile = document.createElement("div");
      tile.className = "node-tile";
      tile.dataset.status = node.status;
      const pending = node.pending_batches ? ` · ${node.pending_batches} waiting` : "";
      tile.append(
        Object.assign(document.createElement("strong"), { textContent: name(node), title: name(node) }),
        Object.assign(document.createElement("span"), { className: "mono small muted", textContent: node.node_id }),
        Object.assign(document.createElement("div"), { className: "small", textContent: `${node.status === "ONLINE" ? "Online" : "Offline"}${pending}` }),
      );
      return tile;
    }),
  );
  $("nodes-empty").hidden = nodes.length > 0;
  const barangays = new Set(nodes.map((n) => n.barangay_id)).size;
  $("nodes-count").textContent = nodes.length ? `${nodes.length} nodes · ${barangays} of 26 barangays` : "";
}

const name = (node) => barangayNames[node.barangay_id] ?? node.barangay_id;
