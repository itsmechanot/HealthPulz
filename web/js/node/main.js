// BHS Node page: choose a barangay, generate cases, send them to the RHU, show status and alerts.

import * as api from "../common/api.js";
import { newNodeId, uuid4 } from "../common/ids.js";
import { createCombobox } from "./combobox.js";
import { NodeConnection } from "./connection.js";
import { realisticWeek, stressRecords } from "./generator.js";
import * as outbox from "./outbox.js";
import { Sender } from "./sender.js";

const PROTOCOL_VERSION = 1;
const MAX_RECORDS_PER_BATCH = 100;
const MAX_STRESS_RECORDS = 5000;
const MAX_LOG_ENTRIES = 100;
const IDENTITY_KEY = "healthpulz.node";

const $ = (id) => document.getElementById(id);
const totals = { stored: 0, duplicates: 0, rejected: 0 };
let pendingBatches = 0;

main();

async function main() {
  let config;
  try {
    config = await api.getConfig();
  } catch (error) {
    $("setup").hidden = false;
    $("setup-error").textContent = `Cannot reach the RHU server. Check the Wi-Fi and reload. (${error.message})`;
    return;
  }

  const identity = loadIdentity();
  const barangay = identity && config.barangays.find((b) => b.barangay_id === identity.barangay_id);
  if (barangay) startStation(config, identity, barangay);
  else showSetup(config);
}

// ---- step 1: choose a barangay ---------------------------------------------

function showSetup(config) {
  $("setup").hidden = false;
  let chosen = null;

  createCombobox({
    input: $("barangay-input"),
    listbox: $("barangay-list"),
    options: config.barangays,
    labelOf: (b) => b.name,
    onSelect: (b) => {
      chosen = b;
      $("start-btn").disabled = !b;
    },
  });

  $("start-btn").addEventListener("click", () => {
    const identity = { node_id: newNodeId(chosen.barangay_id), barangay_id: chosen.barangay_id };
    saveIdentity(identity);
    $("setup").hidden = true;
    startStation(config, identity, chosen);
  });
}

// ---- step 2: the station ---------------------------------------------------

async function startStation(config, identity, barangay) {
  $("station").hidden = false;
  $("station-name").textContent = `Brgy. ${barangay.name}`;
  $("node-id").textContent = identity.node_id;
  document.title = `HealthPulz · ${barangay.name}`;

  let rhuReachable = true;

  const register = async () => {
    try {
      await api.registerNode(identity.node_id, identity.barangay_id);
      return true;
    } catch {
      return false;
    }
  };

  const sender = new Sender(register);
  const connection = new NodeConnection(identity.node_id, () => pendingBatches);

  connection.addEventListener("status", ({ detail: state }) => {
    setConnectionState(state);
    if (state === "online") {
      log("Connected to RHU Naval", "ok");
      sender.pump();
    }
  });
  connection.addEventListener("unregistered", register);
  connection.addEventListener("message", ({ detail: message }) => {
    switch (message.type) {
      case "STORED":
        sender.confirmStored(message);
        break;
      case "ALERT":
        showAlert(message, config);
        connection.send({ type: "ALERT_ACK", alert_id: message.alert_id });
        break;
      case "ERROR":
        log(`RHU error: ${message.reason}`, "error");
        break;
    }
  });

  sender.addEventListener("sent", ({ detail: batch }) => {
    if (!rhuReachable) log("RHU reachable again, sending waiting batches", "ok");
    rhuReachable = true;
    log(`Batch ${shortId(batch.batch_id)} received by RHU (${batch.records.length} cases)`);
  });
  sender.addEventListener("stored", ({ detail: message }) => recordStored(message));
  sender.addEventListener("busy", ({ detail: seconds }) => log(`RHU is busy, retrying in ${seconds}s`));
  sender.addEventListener("offline", () => {
    if (rhuReachable) log("Cannot reach the RHU. Batches are kept and will be sent automatically.", "error");
    rhuReachable = false;
  });
  sender.addEventListener("dropped", ({ detail }) =>
    log(`Batch ${shortId(detail.batch.batch_id)} was refused as malformed and removed`, "error"),
  );
  sender.addEventListener("changed", refreshPending);

  setupGenerator(config, identity, barangay, sender);
  $("change-btn").addEventListener("click", () => {
    const warning = pendingBatches
      ? `\n\n${pendingBatches} batch(es) are still waiting; they will still be delivered.`
      : "";
    if (!confirm(`Switch to a different barangay?${warning}`)) return;
    clearIdentity();
    location.reload();
  });

  if (await register()) log(`Registered as node ${identity.node_id}`);
  await refreshPending();
  connection.connect();
  sender.start();
}

function setupGenerator(config, identity, barangay, sender) {
  let mode = "realistic";
  const modeButtons = document.querySelectorAll("[data-mode]");
  modeButtons.forEach((button) =>
    button.addEventListener("click", () => {
      mode = button.dataset.mode;
      modeButtons.forEach((b) => b.setAttribute("aria-pressed", b === button));
      $("realistic-options").hidden = mode !== "realistic";
      $("stress-options").hidden = mode !== "stress";
    }),
  );

  const diseaseSelect = $("outbreak-disease");
  diseaseSelect.replaceChildren(
    ...config.diseases.map((d) => Object.assign(document.createElement("option"), { value: d.disease_code, textContent: d.name })),
  );
  $("outbreak-toggle").addEventListener("change", (e) => (diseaseSelect.disabled = !e.target.checked));

  $("generate-btn").addEventListener("click", async () => {
    let records;
    if (mode === "stress") {
      const count = Math.min(MAX_STRESS_RECORDS, Math.max(1, Number($("stress-count").value) || 1));
      $("stress-count").value = count;
      records = stressRecords(config.diseases, count);
    } else {
      const outbreak = $("outbreak-toggle").checked ? diseaseSelect.value : null;
      records = realisticWeek(config.diseases, barangay, outbreak);
    }

    if (!records.length) {
      $("generate-result").textContent = "No cases this week. Try again or use Stress test.";
      return;
    }

    const batches = chunk(records, MAX_RECORDS_PER_BATCH).map((part) => ({
      type: "SUBMIT_BATCH",
      protocol_version: PROTOCOL_VERSION,
      node_id: identity.node_id,
      batch_id: uuid4(),
      sent_at: new Date().toISOString(),
      records: part,
      drill: mode === "stress", // stress-test data is stored but not analyzed, so it can't trigger alerts
    }));
    await sender.enqueue(batches);

    $("generate-result").textContent =
      `Generated ${records.length} cases in ${batches.length} batch(es): ${summarize(records, config.diseases)}`;
    log(`Generated ${records.length} cases, ${batches.length} batch(es) queued`);
  });
}

// ---- rendering -------------------------------------------------------------

function recordStored(message) {
  // From the WebSocket `rejected` is a list; from a status check it is a count.
  const rejected = Array.isArray(message.rejected) ? message.rejected.length : message.rejected ?? 0;
  totals.stored += message.accepted;
  totals.duplicates += message.duplicates;
  totals.rejected += rejected;
  $("stat-stored").textContent = totals.stored.toLocaleString();
  $("stat-duplicates").textContent = totals.duplicates.toLocaleString();
  $("stat-rejected").textContent = totals.rejected.toLocaleString();

  const parts = [`${message.accepted} stored`];
  if (message.duplicates) parts.push(`${message.duplicates} duplicate`);
  if (rejected) parts.push(`${rejected} rejected`);
  log(`Batch ${shortId(message.batch_id)} confirmed: ${parts.join(", ")}`, "ok");
  if (Array.isArray(message.rejected)) {
    for (const r of message.rejected) log(`Case ${shortId(r.record_id)} rejected: ${r.reason}`, "error");
  }
}

async function refreshPending() {
  pendingBatches = await outbox.countBatches();
  $("stat-pending").textContent = pendingBatches;
}

const alertCards = new Map(); // alert_id -> card; an alert can arrive again (reconnect, escalation)

function showAlert(alert, config) {
  const disease = config.diseases.find((d) => d.disease_code === alert.disease_code)?.name ?? alert.disease_code;
  const where = alert.scope === "NEIGHBOR"
    ? `Nearby: Brgy. ${config.barangays.find((b) => b.barangay_id === alert.barangay_id)?.name ?? alert.barangay_id}`
    : "In your barangay";

  const isNew = !alertCards.has(alert.alert_id);
  const card = alertCards.get(alert.alert_id) ?? document.createElement("div");
  card.className = "alert-card";
  card.dataset.level = alert.level;
  card.replaceChildren(
    Object.assign(document.createElement("strong"), { textContent: `${alert.level}: ${disease}` }),
    Object.assign(document.createElement("span"), { className: "small muted", textContent: where }),
    Object.assign(document.createElement("div"), { textContent: alert.message }),
  );
  if (isNew) {
    alertCards.set(alert.alert_id, card);
    $("alerts").querySelector(".empty")?.remove();
    $("alerts").prepend(card);
  }
  log(`${isNew ? "Alert received" : "Alert updated"}: ${disease} (${alert.level})`, "error");
}

function setConnectionState(state) {
  const labels = { online: "Online", connecting: "Connecting…", offline: "Offline" };
  $("connection").dataset.state = state;
  $("connection").textContent = labels[state];
}

function log(text, kind = "") {
  const item = document.createElement("li");
  const time = Object.assign(document.createElement("time"), { textContent: new Date().toLocaleTimeString() });
  item.append(time, Object.assign(document.createElement("span"), { textContent: text, className: kind }));
  $("log").prepend(item);
  while ($("log").children.length > MAX_LOG_ENTRIES) $("log").lastChild.remove();
}

// ---- helpers ---------------------------------------------------------------

function chunk(items, size) {
  const parts = [];
  for (let i = 0; i < items.length; i += size) parts.push(items.slice(i, i + size));
  return parts;
}

function summarize(records, diseases) {
  const counts = {};
  for (const r of records) counts[r.disease_code] = (counts[r.disease_code] ?? 0) + 1;
  return diseases
    .filter((d) => counts[d.disease_code])
    .map((d) => `${d.name} ${counts[d.disease_code]}`)
    .join(", ");
}

const shortId = (id) => id.slice(0, 8);

function loadIdentity() {
  try {
    return JSON.parse(localStorage.getItem(IDENTITY_KEY));
  } catch {
    return null;
  }
}

function saveIdentity(identity) {
  try {
    localStorage.setItem(IDENTITY_KEY, JSON.stringify(identity));
  } catch {
    // Without storage the node gets a new ID on every reload; it still works.
  }
}

function clearIdentity() {
  try {
    localStorage.removeItem(IDENTITY_KEY);
  } catch {
    // nothing stored, nothing to clear
  }
}
