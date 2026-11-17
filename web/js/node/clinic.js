// BHU clinic screens: patients and their consultations (records live on the server, see records.js).

import { diseaseOptions } from "../common/diseases.js";
import { el } from "../common/dom.js";
import { confirmAction, icon, toast } from "../common/shell.js";
import * as records from "./records.js";

const OTHER_CODE = "OTH";
const STATUS_LABELS = { SUSPECTED: "Suspected", CONFIRMED: "Confirmed" };
// Fields compared when a consultation is corrected, with the names shown in its history.
const FIELD_LABELS = {
  visit_date: "Consultation date",
  onset_date: "Symptom onset",
  chief_complaint: "Presenting concern",
  temperature: "Temperature",
  bp_systolic: "BP systolic",
  bp_diastolic: "BP diastolic",
  pulse: "Pulse",
  respiratory_rate: "Respiratory rate",
  weight: "Weight",
  height: "Height",
  findings: "Findings",
  case_status: "Case status",
  treatment: "Treatment",
  disposition: "Disposition",
  follow_up_date: "Follow-up date",
  attended_by: "Attended by",
};
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

export function createClinic({ config, router }) {
  const diseases = new Map(config.diseases.map((d) => [d.disease_code, d]));
  let visitClient = null;
  let editingClient = null;
  let correctingVisit = null;
  let shownVisit = null;
  // Chosen when a form opens and kept across retries, so a retry can't save a record twice.
  let newClientId = null;
  let newVisitId = null;

  const diagnosisLabel = (c) => (c.diagnosis_code === OTHER_CODE ? c.diagnosis_text : diseases.get(c.diagnosis_code)?.name ?? c.diagnosis_code);

  // ---- reusable pieces --------------------------------------------------------------

  function syncLabel(consultation) {
    const [label, level] = SYNC_LABELS[consultation.sync];
    if (!consultation.voided || consultation.sync === records.SYNC.REJECTED) return [label, level];
    return consultation.sync === records.SYNC.WAITING ? ["Void waiting to reach the RHU", "ALERT"] : ["Voided", "RESOLVED"];
  }

  function syncBadge(consultation) {
    const [label, level] = syncLabel(consultation);
    return el("span", { className: "badge", dataset: { level }, textContent: label });
  }

  const wasCorrected = (c) => (c.history ?? []).some((entry) => entry.action === "CORRECTED");

  function visitRow(consultation, client) {
    return el(
      "a",
      { className: `record-row${consultation.voided ? " voided" : ""}`, href: `#/visit/${consultation.consultation_id}` },
      el(
        "div",
        { className: "grow" },
        el("strong", { textContent: client ? records.fullName(client) : diagnosisLabel(consultation) }),
        wasCorrected(consultation) ? el("span", { className: "chip", textContent: "Corrected", style: "margin-left: 6px" }) : null,
        el("div", {
          className: "muted small",
          textContent: `${formatDate(consultation.visit_date)}${client ? ` · ${diagnosisLabel(consultation)}` : ` · ${consultation.chief_complaint}`}`,
        }),
      ),
      syncBadge(consultation),
    );
  }

  /** One consultation in full, for the patient profile. */
  function consultationProfile(consultation) {
    const bp = consultation.bp_systolic || consultation.bp_diastolic
      ? `${consultation.bp_systolic || "—"}/${consultation.bp_diastolic || "—"} mmHg`
      : "";
    const withUnit = (value, unit) => (value ? `${value} ${unit}` : "");
    const disease = diseases.get(consultation.diagnosis_code);
    const details = [
      ["Symptom onset", consultation.onset_date && formatDate(consultation.onset_date)],
      ["Assessment / findings", consultation.findings],
      ["Case status", disease?.tracked ? STATUS_LABELS[consultation.case_status] : "Not a surveillance case"],
      ["Temperature", withUnit(consultation.temperature, "°C")],
      ["Blood pressure", bp],
      ["Pulse", withUnit(consultation.pulse, "/min")],
      ["Respiratory rate", withUnit(consultation.respiratory_rate, "/min")],
      ["Weight", withUnit(consultation.weight, "kg")],
      ["Height", withUnit(consultation.height, "cm")],
      ["Treatment / medication", consultation.treatment],
      ["Disposition", consultation.disposition],
      ["Follow-up", consultation.follow_up_date && formatDate(consultation.follow_up_date)],
      ["Attended by", consultation.attended_by],
      ["Report status", syncLabel(consultation)[0]],
      ["RHU response", consultation.sync_detail],
    ].filter(([, value]) => value);

    return el(
      "article",
      { className: `patient-consultation${consultation.voided ? " voided" : ""}` },
      el(
        "div",
        { className: "patient-consultation-heading" },
        el("h3", { textContent: `${formatDate(consultation.visit_date)} · ${diagnosisLabel(consultation)}` }),
        wasCorrected(consultation) ? el("span", { className: "chip", textContent: "Corrected" }) : null,
        consultation.voided ? el("span", { className: "chip", textContent: "Voided" }) : null,
      ),
      el("p", { className: "patient-consultation-complaint", textContent: consultation.chief_complaint || "No complaint recorded" }),
      el("dl", { className: "kv-grid" }, ...details.flatMap(([label, value]) => [el("dt", { textContent: label }), el("dd", { textContent: value })])),
      ...changeHistory(consultation),
      el(
        "div",
        { className: "patient-consultation-actions" },
        consultation.voided
          ? null
          : el("a", { className: "btn btn-ghost btn-small", href: `#/visit-form/${consultation.client_id}/${consultation.consultation_id}`, textContent: "Correct consultation" }),
        el("a", { className: "link-btn small", href: `#/visit/${consultation.consultation_id}`, textContent: "Open record" }),
      ),
    );
  }

  const empty = (text) => el("p", { className: "empty", textContent: text });

  // ---- client list -------------------------------------------------------------------

  async function showClients() {
    const [clients, consultations] = await Promise.all([records.listClients(), records.listConsultations()]);
    const lastVisit = new Map();
    for (const c of consultations) if (!c.voided && !lastVisit.has(c.client_id)) lastVisit.set(c.client_id, c.visit_date);

    const query = $("client-search").value.trim().toLowerCase();
    const matches = clients
      .filter((c) => !query || `${records.fullName(c)} ${c.first_name} ${c.last_name} ${c.contact ?? ""}`.toLowerCase().includes(query))
      .sort((a, b) => records.fullName(a).localeCompare(records.fullName(b)));

    $("client-list").replaceChildren(
      ...(matches.length
        ? matches.map((c) => {
            const visit = lastVisit.get(c.client_id);
            return el(
              "div",
              { className: "record-row patient-record-row" },
              el(
                "a",
                { className: "patient-row-main", href: `#/client/${c.client_id}` },
                el(
                  "div",
                  { className: "grow" },
                  el("strong", { textContent: records.fullName(c) }),
                  el("div", {
                    className: "muted small",
                    textContent: `${records.ageOn(c.birthdate, today())} years · ${sexLabel(c.sex)} · Purok ${c.purok} · ${visit ? `last consultation ${formatDate(visit)}` : "no consultations yet"}`,
                  }),
                ),
                icon("arrow"),
              ),
              el("button", {
                className: "patient-edit-btn",
                type: "button",
                title: `Edit ${records.fullName(c)}`,
                "aria-label": `Edit information for ${records.fullName(c)}`,
                textContent: "✎",
                onclick: () => router.go("client-form", c.client_id),
              }),
            );
          })
        : [empty(clients.length ? "No patient matches your search." : 'No patients registered yet. Select "Register patient" to add one.')]),
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
      ["Patient ID", client.client_id],
      ["Date of birth", formatDate(client.birthdate)],
      ["Sex", sexLabel(client.sex)],
      ["Purok", client.purok],
      ["Civil status", client.civil_status],
      ["Street / sitio", client.address],
      ["Contact number", client.contact],
      ["PhilHealth number", client.philhealth],
    ];
    $("profile-details").replaceChildren(
      ...details.flatMap(([label, value]) => [el("dt", { textContent: label }), el("dd", { textContent: value || "—" })]),
    );

    const visits = await records.listClientConsultations(client.client_id);
    $("profile-history-empty").hidden = visits.length > 0;
    $("profile-visits").replaceChildren(...visits.map(consultationProfile));
  }

  // ---- client form ----------------------------------------------------------------------

  async function showClientForm(clientId) {
    const form = $("client-form");
    form.reset();
    $("client-form-error").textContent = "";
    editingClient = clientId ? await records.getClient(clientId) : null;
    if (clientId && !editingClient) return router.go("clients");
    newClientId = records.newId();

    $("client-form-title").lastChild.textContent = editingClient ? "Edit patient record" : "Register patient";
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
    birthdate.setCustomValidity(birthdate.value && records.ageOn(birthdate.value, today()) > 110 ? "Verify the date of birth. The recorded age must be 110 years or younger." : "");
    if (!form.reportValidity()) return;

    const values = Object.fromEntries(new FormData(form));
    const fields = {
      ...values,
      last_name: values.last_name.trim(),
      first_name: values.first_name.trim(),
      middle_name: values.middle_name.trim(),
      purok: Number(values.purok),
    };
    const save = async () => {
      // The server re-sends the patient's reports if the birthdate or sex changed.
      const saved = await records.saveClient(editingClient?.client_id ?? newClientId, fields);
      router.go("client", saved.client_id);
    };
    if (!editingClient && (await findDuplicate(fields))) {
      await confirmAction({
        title: "Register another patient?",
        message: "A patient with the same name and date of birth is already registered. Confirm this is a different person before creating another record.",
        type: "warning",
        confirmLabel: "Register anyway",
        successTitle: "Patient registered",
        action: save,
      });
      return;
    }
    await submitting(form, $("client-form-error"), save);
  });

  /** Runs a save with the form's buttons disabled; a failure is shown and the form keeps its contents. */
  async function submitting(form, errorElement, save) {
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    errorElement.textContent = "";
    try {
      await save();
    } catch (error) {
      errorElement.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  async function findDuplicate(client) {
    const same = (a, b) => a.trim().toLowerCase() === b.trim().toLowerCase();
    return (await records.listClients()).find(
      (c) => same(c.last_name, client.last_name) && same(c.first_name, client.first_name) && c.birthdate === client.birthdate,
    );
  }

  // ---- consultation form ------------------------------------------------------------------------

  const diagnosisSelect = $("v-diagnosis");
  diagnosisSelect.replaceChildren(
    el("option", { value: "", textContent: "Select…" }),
    ...diseaseOptions(config.diseases.filter((d) => d.tracked)),
    el("option", { value: OTHER_CODE, textContent: "Other (specify)" }),
  );

  /** param: "<clientId>" for a new consultation, "<clientId>/<consultationId>" to correct one. */
  async function showVisitForm(param) {
    const [clientId, consultationId] = (param ?? "").split("/");
    visitClient = clientId && (await records.getClient(clientId));
    if (!visitClient) return router.go("clients");
    correctingVisit = consultationId ? await records.getConsultation(consultationId) : null;
    if (consultationId && (!correctingVisit || correctingVisit.voided)) return router.go("client", clientId);

    const form = $("visit-form");
    form.reset();
    $("visit-form-error").textContent = "";
    newVisitId = records.newId();
    form.elements.visit_date.value = today();
    form.elements.visit_date.max = today();
    form.elements.onset_date.max = today();
    form.elements.correction_reason.setCustomValidity("");

    const correcting = Boolean(correctingVisit);
    $("visit-form-title").lastChild.textContent = correcting ? "Correct consultation" : "Add consultation";
    $("visit-submit").textContent = correcting ? "Save correction and send report" : "Save consultation and send report";
    $("visit-reason-card").hidden = !correcting;
    form.elements.correction_reason.required = correcting;
    if (correcting) {
      for (const field of form.elements) {
        if (field.name && correctingVisit[field.name] != null) field.value = correctingVisit[field.name];
      }
    }
    $("visit-form-cancel").href = correcting ? `#/visit/${correctingVisit.consultation_id}` : `#/client/${visitClient.client_id}`;
    $("visit-client").textContent = `${records.fullName(visitClient)} · ${records.ageOn(visitClient.birthdate, today())} years old · ${sexLabel(visitClient.sex)} · Purok ${visitClient.purok}`;
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
    // Onset dates matter for spotting outbreaks; long-term conditions may not have a clear one.
    const needsOnset = Boolean(diseases.get(code)?.threshold_alerts || diseases.get(code)?.single_case_alert);
    form.elements.onset_date.required = needsOnset;
    $("v-onset-required").textContent = needsOnset ? "*" : "(optional)";
    $("v-urgent-note").hidden = !diseases.get(code)?.single_case_alert;
    if (visitClient) renderPreview(form, code);
  }

  function renderPreview(form, code) {
    const visitDate = form.elements.visit_date.value;
    const age = visitDate ? records.ageOn(visitClient.birthdate, visitDate) : "–";
    const disease = diseases.get(code);
    const rows = [
      ["Disease", disease ? (code === OTHER_CODE ? "Other consultation" : disease.name) : "–"],
      ["Status", disease?.tracked ? STATUS_LABELS[form.elements.case_status.value] : disease ? "Not applicable" : "–"],
      ["Consultation date", visitDate || "–"],
      ["Symptom onset", form.elements.onset_date.value || visitDate || "–"],
      ["Age", age],
      ["Sex", sexLabel(visitClient.sex)],
      ["Purok", correctingVisit?.purok ?? visitClient.purok],
    ];
    $("visit-preview").replaceChildren(
      ...rows.map(([label, value]) => el("div", {}, el("span", { className: "muted small", textContent: label }), el("strong", { textContent: value }))),
    );
  }

  $("visit-form").addEventListener("input", () => {
    $("visit-form").elements.correction_reason.setCustomValidity("");
    updateVisitForm();
  });

  /** What differs between a saved consultation and the corrected fields, for its history. */
  function describeChanges(before, after) {
    const changes = Object.entries(FIELD_LABELS)
      .filter(([key]) => String(before[key] ?? "") !== String(after[key] ?? ""))
      .map(([key, field]) => {
        const shown = (value) => (key === "case_status" ? STATUS_LABELS[value] : String(value ?? ""));
        return { field, from: shown(before[key]), to: shown(after[key]) };
      });
    const oldDiagnosis = diagnosisLabel(before);
    const newDiagnosis = diagnosisLabel(after);
    if (oldDiagnosis !== newDiagnosis) changes.push({ field: "Diagnosis", from: oldDiagnosis, to: newDiagnosis });
    return changes;
  }

  $("visit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const { visit_date: visitDate, onset_date: onsetDate } = form.elements;
    visitDate.setCustomValidity(visitDate.value && visitDate.value < visitClient.birthdate ? "The consultation date cannot be before the patient's birth date." : "");
    onsetDate.setCustomValidity(onsetDate.value && onsetDate.value > visitDate.value ? "Symptom onset cannot be after the consultation date." : "");
    if (!form.reportValidity()) return;

    const { correction_reason: reasonValue, ...values } = Object.fromEntries(new FormData(form));
    const tracked = diseases.get(values.diagnosis_code).tracked;
    const fields = {
      ...values,
      chief_complaint: values.chief_complaint.trim(),
      diagnosis_text: tracked ? "" : values.diagnosis_text.trim(),
      case_status: tracked ? values.case_status : "CONFIRMED", // the protocol requires a status; "Other" visits aren't surveillance cases
    };

    let changes = [];
    if (correctingVisit) {
      changes = describeChanges(correctingVisit, fields);
      const reason = form.elements.correction_reason;
      reason.setCustomValidity(changes.length ? "" : "No changes have been made to this consultation.");
      if (!form.reportValidity()) return;
    }

    await submitting(form, $("visit-form-error"), async () => {
      const saved = correctingVisit
        ? await records.correctConsultation(correctingVisit.consultation_id, fields, reasonValue.trim(), changes)
        : await records.saveConsultation(visitClient.client_id, newVisitId, fields);
      router.go("visit", saved.consultation_id);
    });
  });

  // ---- visit list -----------------------------------------------------------------------------

  async function showVisits() {
    const [consultations, clients] = await Promise.all([records.listConsultations(), records.listClients()]);
    const clientsById = new Map(clients.map((c) => [c.client_id, c]));

    $("visits-today").textContent = consultations.filter((c) => c.visit_date === today() && !c.voided).length;
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
        : [empty(consultations.length ? "No consultation matches your search." : "No consultations yet. Open a patient to record one.")]),
    );
  }

  $("visit-search").addEventListener("input", showVisits);

  // ---- visit record -----------------------------------------------------------------------------

  async function showVisit(id) {
    const v = id && (await records.getConsultation(id));
    if (!v) return router.go("visits");
    const client = await records.getClient(v.client_id);
    shownVisit = { visit: v, client };

    $("visit-correct").href = `#/visit-form/${v.client_id}/${v.consultation_id}`;
    $("visit-actions").hidden = v.voided;
    $("visit-title").textContent = `${diagnosisLabel(v)} · ${formatDate(v.visit_date)}`;
    $("visit-client-link").textContent = client ? `${records.fullName(client)} →` : "";
    $("visit-client-link").href = `#/client/${v.client_id}`;
    $("visit-back").href = `#/client/${v.client_id}`;
    $("visit-back").textContent = "← Back to patient record";
    const [label, level] = syncLabel(v);
    $("visit-sync").textContent = label;
    $("visit-sync").dataset.level = level;

    const bp = v.bp_systolic || v.bp_diastolic ? `${v.bp_systolic || "–"}/${v.bp_diastolic || "–"} mmHg` : "";
    const withUnit = (value, unit) => (value ? `${value} ${unit}` : "");
    const disease = diseases.get(v.diagnosis_code);
    const sections = [
      ["Consultation", [["Presenting concern", v.chief_complaint], ["Symptom onset", v.onset_date && formatDate(v.onset_date)]]],
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
    const voidEntry = v.voided ? v.history.findLast((entry) => entry.action === "VOIDED") : null;
    $("visit-details").replaceChildren(
      ...(voidEntry ? [el("p", { className: "note note-danger", textContent: `Voided: ${voidEntry.reason}. This consultation is excluded from RHU surveillance counts.` })] : []),
      ...(v.sync_detail ? [el("p", { className: "note note-danger", textContent: `The RHU did not count this report: ${v.sync_detail}` })] : []),
      ...sections.map(([title, rows]) =>
        el(
          "div",
          {},
          el("h3", { textContent: title }),
          el("dl", { className: "kv-grid" }, ...rows.flatMap(([k, value]) => [el("dt", { textContent: k }), el("dd", { textContent: value || "—" })])),
        ),
      ),
      ...changeHistory(v),
    );
  }

  /** Who changed what is kept on this device; only the corrected report goes to the RHU. */
  function changeHistory(v) {
    const entries = v.history ?? [];
    if (!entries.length) return [];
    const titles = { CORRECTED: "Corrected", VOIDED: "Voided", CLIENT_CORRECTED: "Report re-sent" };
    return [
      el(
        "div",
        {},
        el("h3", { textContent: "Record change history" }),
        el(
          "ul",
          { className: "change-list" },
          ...[...entries].reverse().map((entry) =>
            el(
              "li",
              {},
              el("strong", { textContent: `${titles[entry.action]} · ${new Date(entry.at).toLocaleString()}` }),
              el("div", { className: "muted small", textContent: `Reason: ${entry.reason}` }),
              ...entry.changes.map((c) => el("div", { className: "small", textContent: `${c.field}: ${c.from || "—"} → ${c.to || "—"}` })),
            ),
          ),
        ),
      ),
    ];
  }

  // Void: the record stays here marked as voided, and the RHU stops counting it.
  const voidDialog = $("void-dialog");
  $("visit-void").addEventListener("click", () => {
    $("void-reason").value = "";
    $("void-error").textContent = "";
    voidDialog.showModal();
  });
  $("void-cancel").addEventListener("click", () => voidDialog.close());
  $("void-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const reason = $("void-reason");
    reason.setCustomValidity(reason.value.trim() ? "" : "Please give the reason.");
    if (!reason.reportValidity()) return;
    const { visit } = shownVisit;
    $("void-error").textContent = "";
    try {
      await records.voidConsultation(visit.consultation_id, reason.value.trim());
    } catch (error) {
      $("void-error").textContent = error.message;
      return;
    }
    voidDialog.close();
    showVisit(visit.consultation_id);
  });
  $("void-reason").addEventListener("input", (event) => event.target.setCustomValidity(""));

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

  /** Loading a screen can fail if the server is out of reach; say so instead of showing nothing. */
  const show = (loading) =>
    loading?.catch((error) => toast({ title: "Couldn't load this screen", body: error.message }));

  return {
    /** Call when the screen changes. */
    show(name, param) {
      lastScreen = { name, param };
      show(handlers[name]?.(param));
    },
    /** Call when a report's delivery status changes: refreshes what's on screen (never a form). */
    refresh() {
      const { name, param } = lastScreen;
      if (["clients", "client", "visits", "visit"].includes(name)) show(handlers[name](param));
    },
  };
}
