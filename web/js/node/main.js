// BHU page: client records, delivery status, alerts, trends and Demo Tools.

import * as api from "../common/api.js";
import { uuid4 } from "../common/ids.js";
import { diseaseOptions } from "../common/diseases.js";
import { applyPlace, fillPlace } from "../common/place.js";
import { createRouter, setupLogout, setupMenu, toast } from "../common/shell.js";
import { createClinic } from "./clinic.js";
import { browserRecords, clearBrowserRecords } from "./db.js";
import { NodeConnection } from "./connection.js";
import { realisticWeek, stressRecords } from "./generator.js";
import * as outbox from "./outbox.js";
import * as records from "./records.js";
import { Sender } from "./sender.js";
import { startTrendsPanel } from "./trends.js";

const PROTOCOL_VERSION = 1;
const MAX_RECORDS_PER_BATCH = 100;
const MAX_STRESS_RECORDS = 5000;
const MAX_LOG_ENTRIES = 100;
const ALERT_GROUPING_MS = 500;

const $ = (id) => document.getElementById(id);
const totals = { stored: 0, duplicates: 0, rejected: 0 };
let stationId = null;
let pendingBatches = 0;
let router = null;
let unseenAlerts = 0;
let alertsToAnnounce = [];
let announceTimer = null;

main();

async function main() {
  let config, account;
  try {
    [config, account] = await Promise.all([api.getConfig(), api.getAccount()]);
  } catch (error) {
    document.querySelector('[data-screen="unreachable"]').hidden = false;
    $("unreachable-error").textContent = `Cannot reach the RHU server. Check the Wi-Fi and reload. (${error.message})`;
    return;
  }
  const barangay = config.barangays.find((b) => b.barangay_id === account.barangay_id);
  startStation(config, identityOf(barangay.barangay_id), barangay);
}

async function startStation(config, identity, barangay) {
  stationId = identity.node_id;
  document.querySelector(".app").classList.remove("setup-mode");
  document.querySelector(".menu-btn").hidden = false;
  $("station-name").textContent = `Brgy. ${barangay.name}`;
  applyPlace(config.municipality);
  // Phones hide the RHU part so the barangay name fits.
  $("station-sub").replaceChildren("Barangay Health Unit", Object.assign(document.createElement("span"), {
    className: "station-sub-more", textContent: fillPlace(" · connected to {rhu}"),
  }));
  records.useBarangay(identity.barangay_id);
  setBarangayLogo($("station-avatar"), barangay);
  $("station-mark").hidden = false;
  document.title = `HealthPulz · ${barangay.name}`;
  setupMenu();
  let clinic = null;
  router = createRouter("clients", (screen, param) => {
    if (screen === "alerts") setUnseenAlerts(0);
    clinic?.show(screen, param);
  });

  let rhuReachable = true;

  const register = async () => {
    try {
      await api.registerNode(identity.node_id, identity.barangay_id);
      return true;
    } catch {
      return false;
    }
  };

  const sender = new Sender(identity.node_id, register);
  const connection = new NodeConnection(identity.node_id, () => pendingBatches);
  const trends = startTrendsPanel(config, barangay);
  clinic = createClinic({ config, router });
  router.start();
  setupBackup(identity, clinic);
  offerToMoveBrowserRecords(identity, clinic);

  connection.addEventListener("status", ({ detail: state }) => {
    setConnectionState(state);
    if (state === "online") {
      log(fillPlace("Connected to {rhu}"), "ok");
      sender.pump();
    }
  });
  connection.addEventListener("unregistered", register);
  connection.addEventListener("message", ({ detail: message }) => {
    switch (message.type) {
      case "STORED":
        // Either this device's demo batch (handled by the sender) or a report from this barangay's records.
        sender.confirmStored(message).then((own) => {
          if (!own) {
            recordStored(message);
            trends.refreshSoon();
          }
          clinic.refresh();
        });
        break;
      case "ALERT":
        // Already acknowledged (re-sent after a reload): list it quietly, no pop-up or vibration.
        showAlert(message, config, { quiet: message.acknowledged });
        if (message.acknowledged) break;
        notifyAlert(message, config);
        connection.send({ type: "ALERT_ACK", alert_id: message.alert_id });
        trends.refreshSoon();
        break;
      case "BURST":
        runBurst(message, config, identity, sender);
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
  sender.addEventListener("stored", ({ detail: message }) => {
    recordStored(message);
    trends.refreshSoon();
  });
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
  // Only unsent Demo Tools batches can be lost on logout.
  setupLogout({
    message: () => pendingBatches
      ? `${pendingBatches} batch(es) are still waiting on this device. They will send the next time this BHU signs in here. Sign out anyway?`
      : "Are you sure you want to sign out of your HealthPulz account?",
  });

  if (await register()) log(`Connected as ${identity.node_id}`);
  await refreshPending();
  connection.connect();
  sender.start();
}

/** Offers to move records an older version kept in this browser into the barangay's account. */
async function offerToMoveBrowserRecords(identity, clinic) {
  const found = await browserRecords(identity.barangay_id);
  if (!found.clients.length) return;
  $("move-banner").hidden = false;
  $("move-count").textContent = `${found.clients.length} patient record(s) and ${found.consultations.length} consultation(s)`;
  $("move-btn").addEventListener("click", async () => {
    $("move-btn").disabled = true;
    try {
      await records.importBackup({ format: "healthpulz-bhu-backup", barangay_id: identity.barangay_id, ...found });
      await clearBrowserRecords(identity.barangay_id);
      $("move-banner").hidden = true;
      clinic.refresh();
    } catch (error) {
      $("move-error").textContent = error.message;
      $("move-btn").disabled = false;
    }
  });
}

function setupBackup(identity, clinic) {
  const result = $("backup-result");

  $("backup-export").addEventListener("click", async () => {
    try {
      const backup = await records.exportBackup();
      const url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" }));
      const link = Object.assign(document.createElement("a"), {
        href: url,
        download: `healthpulz-${identity.barangay_id}-${backup.exported_at.slice(0, 10)}.json`,
      });
      link.click();
      URL.revokeObjectURL(url);
      result.textContent = `Backup downloaded: ${backup.clients.length} patient records and ${backup.consultations.length} consultations.`;
    } catch (error) {
      result.textContent = `Backup failed: ${error.message}`;
    }
  });

  $("backup-import").addEventListener("change", async (event) => {
    const [file] = event.target.files;
    event.target.value = ""; // so choosing the same file again still fires
    if (!file) return;
    try {
      const counts = await records.importBackup(JSON.parse(await file.text()));
      result.textContent = `Restored ${counts.clients} patient records and ${counts.consultations} consultations.`;
      clinic.refresh();
    } catch (error) {
      result.textContent = `Restore failed: ${error.message}`;
    }
  });
}

function setupGenerator(config, identity, barangay, sender) {
  const tracked = config.diseases.filter((d) => d.tracked);
  const canAlert = tracked.filter((d) => d.threshold_alerts || d.single_case_alert);
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
    ...diseaseOptions(canAlert),
  );
  $("outbreak-toggle").addEventListener("change", (e) => (diseaseSelect.disabled = !e.target.checked));

  $("generate-btn").addEventListener("click", async () => {
    let generated;
    if (mode === "stress") {
      const count = Math.min(MAX_STRESS_RECORDS, Math.max(1, Number($("stress-count").value) || 1));
      $("stress-count").value = count;
      generated = stressRecords(tracked, count);
    } else {
      const outbreak = $("outbreak-toggle").checked
        ? { code: diseaseSelect.value, minimumCases: await outbreakSize(diseaseSelect.value, barangay, config) }
        : null;
      generated = realisticWeek(tracked, barangay, outbreak);
    }

    if (!generated.length) {
      $("generate-result").textContent = "No cases this week. Try again or use Stress test.";
      return;
    }

    const batches = toBatches(generated, identity, mode === "stress");
    await sender.enqueue(batches);

    $("generate-result").textContent =
      `Generated ${generated.length} cases in ${batches.length} batch(es): ${summarize(generated, tracked)}`;
    log(`Generated ${generated.length} cases, ${batches.length} batch(es) queued`);
  });
}

/** The RHU told every connected device to send test records now (the System Monitor's Start burst). */
async function runBurst(message, config, identity, sender) {
  const count = Math.min(MAX_STRESS_RECORDS, message.records_per_device);
  const batches = toBatches(stressRecords(config.diseases.filter((d) => d.tracked), count), identity, true);
  log(`RHU started a burst: sending ${count} test cases in ${batches.length} batch(es)`, "ok");
  await sender.enqueue(batches);
}

/** drill: test data, stored but not analyzed, so it can't trigger alerts. */
function toBatches(records, identity, drill) {
  return chunk(records, MAX_RECORDS_PER_BATCH).map((part) => ({
    type: "SUBMIT_BATCH",
    protocol_version: PROTOCOL_VERSION,
    node_id: identity.node_id,
    batch_id: uuid4(),
    sent_at: new Date().toISOString(),
    records: part,
    drill,
  }));
}

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
  if (message.updated) parts.push(`${message.updated} corrected or voided`);
  if (message.duplicates) parts.push(`${message.duplicates} duplicate`);
  if (rejected) parts.push(`${rejected} rejected`);
  log(`Batch ${shortId(message.batch_id)} confirmed: ${parts.join(", ")}`, "ok");
  if (Array.isArray(message.rejected)) {
    for (const r of message.rejected) log(`Case ${shortId(r.record_id)} rejected: ${r.reason}`, "error");
  }
}

async function refreshPending() {
  pendingBatches = (await outbox.allBatches(stationId)).length;
  $("stat-pending").textContent = pendingBatches;
  document.querySelectorAll(".pending-count").forEach((badge) => {
    badge.hidden = pendingBatches === 0;
    badge.textContent = pendingBatches;
  });
}

/** Counts alerts that arrived while the station was on another screen (badge on the Alerts tab). */
function setUnseenAlerts(count) {
  unseenAlerts = count;
  document.querySelectorAll(".alert-count").forEach((badge) => {
    badge.hidden = count === 0;
    badge.textContent = count;
  });
}

function notifyAlert(alert, config) {
  if (router?.current() === "alerts") return;
  setUnseenAlerts(unseenAlerts + 1);
  // Alerts arriving together share one notice, so pop-ups don't pile up.
  alertsToAnnounce.push(alert);
  clearTimeout(announceTimer);
  announceTimer = setTimeout(() => announceAlerts(config), ALERT_GROUPING_MS);
}

function announceAlerts(config) {
  const alerts = alertsToAnnounce;
  alertsToAnnounce = [];
  const [first] = alerts;
  const disease = config.diseases.find((d) => d.disease_code === first.disease_code)?.name ?? first.disease_code;
  const single = alerts.length === 1;
  toast({
    title: single ? `${first.level}: ${disease}` : `${alerts.length} new alerts`,
    body: !single ? "In your barangay and nearby" : first.scope === "NEIGHBOR" ? "In a neighboring barangay" : "In your barangay",
    level: ["URGENT", "EPIDEMIC"].find((level) => alerts.some((a) => a.level === level)) ?? "ALERT",
    actionLabel: "View",
    onAction: () => router.go("alerts"),
  });
}

const alertCards = new Map(); // alert_id -> card; an alert can arrive again (reconnect, escalation)

function showAlert(alert, config, { quiet = false } = {}) {
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
    $("alerts-count").hidden = false;
    $("alerts-count").textContent = `${alertCards.size} alert${alertCards.size > 1 ? "s" : ""}`;
  }
  if (quiet) return;
  // Attention cue: a pulse (unless reduced motion) and a short vibration.
  card.classList.remove("is-new");
  void card.offsetWidth; // restart the animation
  card.classList.add("is-new");
  navigator.vibrate?.([200, 100, 200]);
  log(`${isNew ? "Alert received" : "Alert updated"}: ${disease} (${alert.level})`, "error");
}

const OUTBREAK_FALLBACK_CASES = 12; // when the RHU can't be asked (offline) or has no threshold yet

/** Cases a simulated outbreak needs: comfortably above this week's epidemic threshold. */
async function outbreakSize(diseaseCode, barangay, config) {
  try {
    const { weeks } = await api.fetchJson(api.trendUrl(diseaseCode, barangay.barangay_id, 1));
    // An urgent disease needs only one case; the others go comfortably over the threshold.
    if (config.diseases.find((d) => d.disease_code === diseaseCode)?.single_case_alert) return 1;
    const threshold = weeks.at(-1)?.epidemic_threshold;
    return threshold == null ? OUTBREAK_FALLBACK_CASES : Math.ceil(threshold * 1.3) + 3;
  } catch {
    return OUTBREAK_FALLBACK_CASES;
  }
}

/** "Padre Inocentes Garcia (Pob.)" -> "PI"; "Caraycaray" -> "CA". */
/** The barangay's logo from /img/barangays/<barangay id>.png, or its initials when there is none. */
function setBarangayLogo(container, barangay) {
  const image = Object.assign(document.createElement("img"), { src: `/img/barangays/${barangay.barangay_id}.png`, alt: "" });
  image.addEventListener("error", () => {
    container.classList.add("initials-seal"); // no logo yet: the initials, styled like a seal
    container.textContent = initials(barangay.name);
  }, { once: true });
  container.classList.remove("initials-seal");
  container.replaceChildren(image);
  container.hidden = false;
}

function initials(name) {
  const words = name.replace(/\(.*?\)/g, "").trim().split(/\s+/);
  return (words.length > 1 ? words[0][0] + words[1][0] : name.slice(0, 2)).toUpperCase();
}

function setConnectionState(state) {
  const labels = { online: "Online", connecting: "Connecting…", offline: "Offline" };
  $("connection").dataset.state = state;
  $("connection").textContent = labels[state];
  // Phones: dot only; the words stay as tooltip and for screen readers.
  const words = { online: "Online: connected to the RHU", connecting: "Connecting to the RHU", offline: "Offline: the RHU cannot be reached" }[state];
  Object.assign($("connection-dot").dataset, { state });
  $("connection-dot").title = words;
  $("connection-dot").setAttribute("aria-label", words);
}

function log(text, kind = "") {
  const item = document.createElement("li");
  const time = Object.assign(document.createElement("time"), { textContent: new Date().toLocaleTimeString() });
  item.append(time, Object.assign(document.createElement("span"), { textContent: text, className: kind }));
  $("log").prepend(item);
  while ($("log").children.length > MAX_LOG_ENTRIES) $("log").lastChild.remove();
}

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

/** Every device logged in as a barangay shares one station. */
function identityOf(barangayId) {
  return { node_id: `${barangayId}-MAIN`, barangay_id: barangayId };
}
