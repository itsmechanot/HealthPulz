// The BHU's clinic screens: clients, client profile, client form, checkup form, visit
// list and visit record. Everything is read from and saved to this device (records.js).

import { el } from "../common/dom.js";
import { icon } from "../common/shell.js";
import * as records from "./records.js";

const OTHER_CODE = "OTH";
const STATUS_LABELS = { SUSPECTED: "Suspected", CONFIRMED: "Confirmed" };
const SYNC_LABELS = {
  [records.SYNC.WAITING]: ["Waiting to reach the RHU", "ALERT"],
  [records.SYNC.STORED]: ["Sent to the RHU", "OK"],
  [records.SYNC.REJECTED]: ["Rejected by the RHU", "EPIDEMIC"],
};

const $ = (id) => document.getElementById(id);

/** Today in the device's local time as YYYY-MM-DD. */
function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const formatDate = (iso) => new Date(`${iso}T00:00`).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
const sexLabel = (code) => ({ F: "Female", M: "Male" })[code] ?? code;

export function createClinic({ config, identity, router, onSaved }) {
  const barangayId = identity.barangay_id;
  const diseases = new Map(config.diseases.map((d) => [d.disease_code, d]));
  let visitClient = null;
  let editingClient = null;

  const diagnosisLabel = (c) => (c.diagnosis_code === OTHER_CODE ? c.diagnosis_text : diseases.get(c.diagnosis_code)?.name ?? c.diagnosis_code);

  // ---- reusable pieces --------------------------------------------------------------

  function syncBadge(consultation) {
    const [label, level] = SYNC_LABELS[consultation.sync];
    return el("span", { className: "badge", dataset: { level }, textContent: label });
  }

  function visitRow(consultation, client) {
    return el(
      "a",
      { className: "record-row", href: `#/visit/${consultation.consultation_id}` },
      el(
        "div",
        { className: "grow" },
        el("strong", { textContent: client ? records.fullName(client) : diagnosisLabel(consultation) }),
        el("div", {
          className: "muted small",
          textContent: `${formatDate(consultation.visit_date)}${client ? ` · ${diagnosisLabel(consultation)}` : ` · ${consultation.chief_complaint}`}`,
        }),
      ),
      syncBadge(consultation),
    );
  }

  const empty = (text) => el("p", { className: "empty", textContent: text });

  // ---- client list -------------------------------------------------------------------

  async function showClients() {
    const available = await records.isAvailable();
    $("storage-warning").hidden = available;
    $("new-client-btn").hidden = !available;
    if (!available) return;

    const [clients, consultations] = await Promise.all([records.listClients(barangayId), records.listConsultations(barangayId)]);
    const lastVisit = new Map();
    for (const c of consultations) if (!lastVisit.has(c.client_id)) lastVisit.set(c.client_id, c.visit_date);

    const query = $("client-search").value.trim().toLowerCase();
    const matches = clients
      .filter((c) => !query || `${records.fullName(c)} ${c.first_name} ${c.last_name} ${c.contact ?? ""}`.toLowerCase().includes(query))
      .sort((a, b) => records.fullName(a).localeCompare(records.fullName(b)));

    $("client-list").replaceChildren(
      ...(matches.length
        ? matches.map((c) => {
            const visit = lastVisit.get(c.client_id);
            return el(
              "a",
              { className: "record-row", href: `#/client/${c.client_id}` },
              el(
                "div",
                { className: "grow" },
                el("strong", { textContent: records.fullName(c) }),
                el("div", {
                  className: "muted small",
                  textContent: `${records.ageOn(c.birthdate, today())} yrs · ${sexLabel(c.sex)} · Purok ${c.purok} · ${visit ? `last checkup ${formatDate(visit)}` : "no checkups yet"}`,
                }),
              ),
              icon("arrow"),
            );
          })
        : [empty(clients.length ? "No client matches your search." : 'No clients yet. Press "New client" to register the first one.')]),
    );
  }

  $("client-search").addEventListener("input", showClients);

  // ---- client profile ------------------------------------------------------------------

  async function showClient(clientId) {
    const client = clientId && (await records.getClient(clientId));
    if (!client) return router.go("clients");

    $("profile-avatar").textContent = `${client.first_name[0]}${client.last_name[0]}`.toUpperCase();
    $("profile-name").textContent = records.fullName(client);
    $("profile-summary").textContent = `${records.ageOn(client.birthdate, today())} years old · ${sexLabel(client.sex)} · Purok ${client.purok}`;
    $("profile-edit").href = `#/client-form/${client.client_id}`;
    $("profile-new-visit").href = `#/visit-form/${client.client_id}`;

    const details = [
      ["Birthdate", formatDate(client.birthdate)],
      ["Civil status", client.civil_status],
      ["Street / sitio", client.address],
      ["Contact number", client.contact],
      ["PhilHealth number", client.philhealth],
    ];
    $("profile-details").replaceChildren(
      ...details.flatMap(([label, value]) => [el("dt", { textContent: label }), el("dd", { textContent: value || "—" })]),
    );

    const visits = await records.listClientConsultations(client.client_id);
    $("profile-visits").replaceChildren(
      ...(visits.length ? visits.map((v) => visitRow(v)) : [empty("No checkups yet.")]),
    );
  }

  // ---- client form ----------------------------------------------------------------------

  async function showClientForm(clientId) {
    const form = $("client-form");
    form.reset();
    editingClient = clientId ? await records.getClient(clientId) : null;
    if (clientId && !editingClient) return router.go("clients");

    $("client-form-title").lastChild.textContent = editingClient ? "Edit client" : "New client";
    $("client-form-cancel").href = editingClient ? `#/client/${editingClient.client_id}` : "#/clients";
    $("c-birthdate").max = today();
    if (editingClient) {
      for (const field of form.elements) {
        if (field.name && editingClient[field.name] != null) field.value = editingClient[field.name];
      }
    }
  }

  $("client-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const birthdate = form.elements.birthdate;
    birthdate.setCustomValidity(birthdate.value && records.ageOn(birthdate.value, today()) > 110 ? "Check the year: age must be 110 or below." : "");
    if (!form.reportValidity()) return;

    const values = Object.fromEntries(new FormData(form));
    const client = {
      ...editingClient,
      ...values,
      last_name: values.last_name.trim(),
      first_name: values.first_name.trim(),
      middle_name: values.middle_name.trim(),
      purok: Number(values.purok),
      barangay_id: barangayId,
    };

    if (!editingClient && (await findDuplicate(client)) && !confirm("A client with the same name and birthdate already exists. Register anyway?")) return;

    const saved = await records.saveClient(client);
    router.go("client", saved.client_id);
  });

  async function findDuplicate(client) {
    const same = (a, b) => a.trim().toLowerCase() === b.trim().toLowerCase();
    return (await records.listClients(barangayId)).find(
      (c) => same(c.last_name, client.last_name) && same(c.first_name, client.first_name) && c.birthdate === client.birthdate,
    );
  }

  // ---- checkup form ------------------------------------------------------------------------

  const diagnosisSelect = $("v-diagnosis");
  diagnosisSelect.replaceChildren(
    el("option", { value: "", textContent: "Select…" }),
    ...config.diseases.filter((d) => d.tracked).map((d) => el("option", { value: d.disease_code, textContent: d.name })),
    el("option", { value: OTHER_CODE, textContent: "Other (specify)" }),
  );

  async function showVisitForm(clientId) {
    visitClient = clientId && (await records.getClient(clientId));
    if (!visitClient) return router.go("clients");

    const form = $("visit-form");
    form.reset();
    form.elements.visit_date.value = today();
    form.elements.visit_date.max = today();
    form.elements.onset_date.max = today();
    $("visit-client").textContent = `${records.fullName(visitClient)} · ${records.ageOn(visitClient.birthdate, today())} years old · ${sexLabel(visitClient.sex)} · Purok ${visitClient.purok}`;
    $("visit-form-cancel").href = `#/client/${visitClient.client_id}`;
    updateVisitForm();
  }

  /** Shows the fields the chosen diagnosis needs, and the case report as it stands. */
  function updateVisitForm() {
    const form = $("visit-form");
    const code = form.elements.diagnosis_code.value;
    const tracked = diseases.get(code)?.tracked ?? false;
    $("v-status-field").hidden = !tracked;
    $("v-text-field").hidden = code !== OTHER_CODE;
    form.elements.diagnosis_text.required = code === OTHER_CODE;
    form.elements.onset_date.required = tracked;
    $("v-onset-required").textContent = tracked ? "*" : "(optional)";
    if (visitClient) renderPreview(form, code);
  }

  function renderPreview(form, code) {
    const visitDate = form.elements.visit_date.value;
    const age = visitDate ? records.ageOn(visitClient.birthdate, visitDate) : "–";
    const disease = diseases.get(code);
    const rows = [
      ["Disease", disease ? (code === OTHER_CODE ? "Other consultation" : disease.name) : "–"],
      ["Status", disease?.tracked ? STATUS_LABELS[form.elements.case_status.value] : disease ? "Not applicable" : "–"],
      ["Visit date", visitDate || "–"],
      ["Symptom onset", form.elements.onset_date.value || visitDate || "–"],
      ["Age", age],
      ["Sex", sexLabel(visitClient.sex)],
      ["Purok", visitClient.purok],
    ];
    $("visit-preview").replaceChildren(
      ...rows.map(([label, value]) => el("div", {}, el("span", { className: "muted small", textContent: label }), el("strong", { textContent: value }))),
    );
  }

  $("visit-form").addEventListener("input", updateVisitForm);

  $("visit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const { visit_date: visitDate, onset_date: onsetDate } = form.elements;
    visitDate.setCustomValidity(visitDate.value && visitDate.value < visitClient.birthdate ? "The visit can't be before the client's birthdate." : "");
    onsetDate.setCustomValidity(onsetDate.value && onsetDate.value > visitDate.value ? "Symptom onset can't be after the visit date." : "");
    if (!form.reportValidity()) return;

    const values = Object.fromEntries(new FormData(form));
    const tracked = diseases.get(values.diagnosis_code).tracked;
    const consultation = await records.saveConsultation(identity.node_id, visitClient, {
      ...values,
      chief_complaint: values.chief_complaint.trim(),
      diagnosis_text: tracked ? "" : values.diagnosis_text.trim(),
      case_status: tracked ? values.case_status : "CONFIRMED", // the protocol requires a status; "Other" visits aren't surveillance cases
    });
    onSaved();
    router.go("visit", consultation.consultation_id);
  });

  // ---- visit list -----------------------------------------------------------------------------

  async function showVisits() {
    if (!(await records.isAvailable())) return;
    const [consultations, clients] = await Promise.all([records.listConsultations(barangayId), records.listClients(barangayId)]);
    const clientsById = new Map(clients.map((c) => [c.client_id, c]));

    $("visits-today").textContent = consultations.filter((c) => c.visit_date === today()).length;
    $("clients-total").textContent = clients.length;
    $("visits-waiting").textContent = consultations.filter((c) => c.sync === records.SYNC.WAITING).length;

    const query = $("visit-search").value.trim().toLowerCase();
    const matches = consultations.filter((c) => {
      const client = clientsById.get(c.client_id);
      return !query || `${client ? records.fullName(client) : ""} ${diagnosisLabel(c)}`.toLowerCase().includes(query);
    });
    $("visit-list").replaceChildren(
      ...(matches.length
        ? matches.map((c) => visitRow(c, clientsById.get(c.client_id)))
        : [empty(consultations.length ? "No checkup matches your search." : "No checkups yet. Open a client to record one.")]),
    );
  }

  $("visit-search").addEventListener("input", showVisits);

  // ---- visit record -----------------------------------------------------------------------------

  async function showVisit(id) {
    const v = id && (await records.getConsultation(id));
    if (!v) return router.go("visits");
    const client = await records.getClient(v.client_id);

    $("visit-title").textContent = `${diagnosisLabel(v)} · ${formatDate(v.visit_date)}`;
    $("visit-client-link").textContent = client ? `${records.fullName(client)} →` : "";
    $("visit-client-link").href = `#/client/${v.client_id}`;
    $("visit-back").href = `#/client/${v.client_id}`;
    $("visit-back").textContent = "← Back to client";
    const [label, level] = SYNC_LABELS[v.sync];
    $("visit-sync").textContent = label;
    $("visit-sync").dataset.level = level;

    const bp = v.bp_systolic || v.bp_diastolic ? `${v.bp_systolic || "–"}/${v.bp_diastolic || "–"} mmHg` : "";
    const withUnit = (value, unit) => (value ? `${value} ${unit}` : "");
    const disease = diseases.get(v.diagnosis_code);
    const sections = [
      ["Visit", [["Chief complaint", v.chief_complaint], ["Symptom onset", v.onset_date && formatDate(v.onset_date)]]],
      ["Vital signs", [
        ["Temperature", withUnit(v.temperature, "°C")],
        ["Blood pressure", bp],
        ["Pulse", withUnit(v.pulse, "/min")],
        ["Respiratory rate", withUnit(v.respiratory_rate, "/min")],
        ["Weight", withUnit(v.weight, "kg")],
        ["Height", withUnit(v.height, "cm")],
      ]],
      ["Findings and diagnosis", [
        ["Findings", v.findings],
        ["Diagnosis", diagnosisLabel(v)],
        ["Case status", disease?.tracked ? STATUS_LABELS[v.case_status] : "Not a surveillance case"],
      ]],
      ["Plan", [
        ["Treatment", v.treatment],
        ["Disposition", v.disposition],
        ["Follow-up", v.follow_up_date && formatDate(v.follow_up_date)],
        ["Attended by", v.attended_by],
      ]],
    ];
    $("visit-details").replaceChildren(
      ...(v.sync_detail ? [el("p", { className: "note note-danger", textContent: `The RHU did not count this report: ${v.sync_detail}` })] : []),
      ...sections.map(([title, rows]) =>
        el(
          "div",
          {},
          el("h3", { textContent: title }),
          el("dl", { className: "kv-grid" }, ...rows.flatMap(([k, value]) => [el("dt", { textContent: k }), el("dd", { textContent: value || "—" })])),
        ),
      ),
    );
  }

  // ---- routing ---------------------------------------------------------------------------------

  let lastScreen = { name: null, param: null };
  const handlers = {
    clients: showClients,
    client: showClient,
    "client-form": showClientForm,
    "visit-form": showVisitForm,
    visits: showVisits,
    visit: showVisit,
  };

  return {
    /** Call when the screen changes. */
    show(name, param) {
      lastScreen = { name, param };
      handlers[name]?.(param);
    },
    /** Call when a report's delivery status changes: refreshes what's on screen (never a form). */
    refresh() {
      const { name, param } = lastScreen;
      if (["clients", "client", "visits", "visit"].includes(name)) handlers[name](param);
    },
  };
}
