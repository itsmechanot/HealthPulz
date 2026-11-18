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
/** "Nov 18", with the year only when it isn't this year. */
const shortDate = (iso) => new Date(`${iso}T00:00`).toLocaleDateString(undefined,
  iso.slice(0, 4) === today().slice(0, 4) ? { month: "short", day: "numeric" } : { year: "numeric", month: "short", day: "numeric" });

/** Whole days from one YYYY-MM-DD to another (positive when `to` is later). */
const daysBetween = (from, to) => Math.round((new Date(`${to}T00:00`) - new Date(`${from}T00:00`)) / 86_400_000);

/** "Today", "Yesterday", "3 days ago", "In 5 days". */
function relativeDay(iso) {
  const days = daysBetween(iso, today());
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days === -1) return "Tomorrow";
  const n = Math.abs(days);
  const span = n >= 60 ? `${Math.round(n / 30)} months` : n >= 14 ? `${Math.round(n / 7)} weeks` : `${n} days`;
  return days > 0 ? `${span} ago` : `In ${span}`;
}

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

  /** One consultation as a timeline entry: when, what it was, how they presented, what was done. */
  function consultationProfile(consultation) {
    const disease = diseases.get(consultation.diagnosis_code);
    const kind = consultation.voided ? "voided" : disease?.single_case_alert ? "urgent" : disease?.tracked ? "tracked" : "other";
    const [syncText, syncLevel] = syncLabel(consultation);

    const vitals = vitalsOf(consultation, visitBirthdate);

    const onsetDays = consultation.onset_date ? daysBetween(consultation.onset_date, consultation.visit_date) : null;
    const plan = [
      consultation.disposition,
      consultation.follow_up_date ? `follow-up ${formatDate(consultation.follow_up_date)}` : "",
    ].filter(Boolean).join(" · ");
    const sections = [["Findings", consultation.findings], ["Treatment", consultation.treatment], ["Plan", plan]].filter(([, v]) => v);

    return el(
      "li",
      { className: "timeline-item", dataset: { kind } },
      el("span", { className: "timeline-dot", "aria-hidden": "true" }),
      el(
        "article",
        { className: "timeline-card" },
        el("div", { className: "timeline-when" },
          el("strong", { textContent: formatDate(consultation.visit_date) }),
          el("span", { className: "muted small", textContent: relativeDay(consultation.visit_date) }),
          el("span", { className: "badge timeline-sync", dataset: { level: syncLevel }, textContent: syncText }),
        ),
        el("div", { className: "timeline-title" },
          el("h3", { textContent: diagnosisLabel(consultation) }),
          disease?.tracked ? el("span", { className: "chip", dataset: { status: consultation.case_status }, textContent: STATUS_LABELS[consultation.case_status] }) : null,
          kind === "urgent" ? el("span", { className: "chip chip-urgent", textContent: "Urgent disease" }) : null,
          wasCorrected(consultation) ? el("span", { className: "chip", textContent: "Corrected" }) : null,
          consultation.voided ? el("span", { className: "chip", textContent: "Voided" }) : null,
        ),
        consultation.chief_complaint
          ? el("blockquote", { className: "timeline-complaint" },
            `“${consultation.chief_complaint}”`,
            onsetDays != null ? el("span", { className: "muted small", textContent: onsetDays === 0 ? " · symptoms started that day" : ` · symptoms for ${onsetDays} day${onsetDays === 1 ? "" : "s"}` }) : null)
          : null,
        vitals.length ? el("div", { className: "vital-chips" }, ...vitals) : null,
        sections.length
          ? el("dl", { className: "timeline-notes" }, ...sections.flatMap(([label, value]) => [el("dt", { textContent: label }), el("dd", { textContent: value })]))
          : null,
        consultation.sync_detail ? el("p", { className: "note note-danger", textContent: `The RHU did not count this report: ${consultation.sync_detail}` }) : null,
        el("div", { className: "timeline-footer" },
          el("span", { className: "muted small", textContent: consultation.attended_by ? `Attended by ${consultation.attended_by}` : "" }),
          el("span", { className: "timeline-actions" },
            consultation.voided ? null : el("a", { className: "btn btn-ghost btn-small", href: `#/visit-form/${consultation.client_id}/${consultation.consultation_id}`, textContent: "Correct" }),
            el("a", { className: "link-btn small", href: `#/visit/${consultation.consultation_id}`, textContent: "Full record →" }),
          ),
        ),
      ),
    );
  }

  /** Vital signs as chips. Out of range is highlighted: adult ranges, and only fever for children
   *  (their normal pulse and breathing run higher). */
  function vitalsOf(c, birthdate) {
    const adult = birthdate ? records.ageOn(birthdate, c.visit_date) >= 18 : true;
    return [
      vital("Temp", c.temperature, "°C", (v) => v >= 37.5 || v < 35),
      c.bp_systolic || c.bp_diastolic
        ? vital("BP", `${c.bp_systolic || "–"}/${c.bp_diastolic || "–"}`, "mmHg", () =>
          adult && (c.bp_systolic >= 140 || c.bp_diastolic >= 90 || (c.bp_systolic && c.bp_systolic < 90)))
        : null,
      vital("Pulse", c.pulse, "/min", (v) => adult && (v > 100 || v < 60)),
      vital("Breathing", c.respiratory_rate, "/min", (v) => adult && (v > 20 || v < 12)),
      vital("Weight", c.weight, "kg"),
      vital("Height", c.height, "cm"),
    ].filter(Boolean);
  }

  /** A vital sign as a chip; out of the normal range is highlighted. */
  function vital(label, value, unit, abnormal = () => false) {
    if (value === "" || value == null) return null;
    const high = abnormal(Number(value));
    return el("span", { className: `vital${high ? " vital-high" : ""}`, title: high ? "Outside the normal range" : "" },
      el("span", { className: "vital-label", textContent: label }),
      el("strong", { textContent: `${value}${unit ? ` ${unit}` : ""}` }),
    );
  }

  let visitBirthdate = null; // the profile's patient, for each consultation's age

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
                  el("div", { className: "small patient-row-facts", textContent: `${records.ageOn(c.birthdate, today())} years · ${sexLabel(c.sex)} · Purok ${c.purok}` }),
                  el("div", { className: "muted small", textContent: visit ? `Last consultation ${formatDate(visit)}` : "No consultations yet" }),
                ),
                icon("arrow"),
              ),
              el("button", {
                className: "patient-edit-btn",
                type: "button",
                title: `Change ${records.fullName(c)}'s name, birthdate, purok or contact details`,
                "aria-label": `Edit patient details of ${records.fullName(c)}`,
                textContent: "✎ Edit details",
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
    visitBirthdate = client.birthdate;

    $("profile-avatar").textContent = `${client.first_name[0]}${client.last_name[0]}`.toUpperCase();
    $("profile-avatar").dataset.sex = client.sex;
    $("profile-name").textContent = records.fullName(client);
    $("profile-summary").replaceChildren(
      ...[`${records.ageOn(client.birthdate, today())} years old`, sexLabel(client.sex), `Purok ${client.purok}`]
        .map((text) => el("span", { className: "profile-chip", textContent: text })),
    );
    $("profile-edit").href = `#/client-form/${client.client_id}`;
    $("profile-new-visit").href = `#/visit-form/${client.client_id}`;

    // Reference details: what is recorded, and one line for what is not.
    const details = [
      ["Date of birth", formatDate(client.birthdate)],
      ["Civil status", client.civil_status],
      ["Street / sitio", client.address],
      ["Contact number", client.contact],
      ["PhilHealth number", client.philhealth],
    ];
    const recorded = details.filter(([, value]) => value);
    const missing = details.filter(([, value]) => !value).map(([label]) => label.toLowerCase());
    $("profile-details").replaceChildren(
      ...recorded.flatMap(([label, value]) => [el("dt", { textContent: label }), el("dd", { textContent: value })]),
    );
    $("profile-missing").hidden = !missing.length;
    $("profile-missing").textContent = `Not recorded yet: ${missing.join(", ")}.`;
    $("profile-id").textContent = `Patient ID ${client.client_id}`;

    const visits = (await records.listClientConsultations(client.client_id))
      .sort((a, b) => b.visit_date.localeCompare(a.visit_date));
    const counted = visits.filter((v) => !v.voided);
    $("profile-history-empty").hidden = visits.length > 0;
    $("profile-visits").replaceChildren(...visits.map(consultationProfile));

    // The story at a glance: what is going on now, then three numbers.
    const recent = counted.find((v) => diseases.get(v.diagnosis_code)?.tracked && daysBetween(v.visit_date, today()) <= 14);
    const followUp = counted.map((v) => v.follow_up_date).filter((d) => d && d >= today()).sort()[0];
    const lines = [];
    if (recent) {
      const disease = diseases.get(recent.diagnosis_code);
      lines.push(statusLine(disease.single_case_alert ? "urgent" : "tracked",
        `${STATUS_LABELS[recent.case_status]} ${disease.name}`,
        `seen ${relativeDay(recent.visit_date).toLowerCase()}, reported to the RHU for surveillance`));
    }
    if (followUp) lines.push(statusLine("follow-up", `Follow-up due ${formatDate(followUp)}`, relativeDay(followUp).toLowerCase()));
    if (!lines.length) {
      lines.push(counted.length
        ? statusLine("ok", "No recent illness under surveillance", `last seen ${relativeDay(counted[0].visit_date).toLowerCase()}`)
        : statusLine("ok", "No consultations yet", "registered patient"));
    }
    $("profile-status").replaceChildren(...lines);

    const fact = (value, label) => el("div", { className: "profile-fact" }, el("strong", { textContent: value }), el("span", { textContent: label }));
    $("profile-facts").replaceChildren(
      fact(String(counted.length), counted.length === 1 ? "consultation" : "consultations"),
      fact(counted.length ? shortDate(counted[0].visit_date) : "–", "last seen"),
      fact(followUp ? shortDate(followUp) : "None", "next follow-up"),
    );
  }

  function statusLine(kind, title, detail) {
    return el("div", { className: "profile-status-line", dataset: { kind } },
      el("span", { className: "profile-status-dot", "aria-hidden": "true" }),
      el("strong", { textContent: title }),
      el("span", { className: "muted small", textContent: detail }),
    );
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
    const disease = diseases.get(v.diagnosis_code);
    const urgent = Boolean(disease?.single_case_alert);
    const age = client ? records.ageOn(client.birthdate, v.visit_date) : null;

    // Who, what, when.
    $("visit-back").href = `#/client/${v.client_id}`;
    $("visit-back").replaceChildren(el("span", { "aria-hidden": "true", textContent: "←" }), " Back to patient profile");
    $("visit-client-link").href = `#/client/${v.client_id}`;
    $("visit-client-link").replaceChildren(
      el("span", { className: "avatar", dataset: { sex: client?.sex ?? "" }, textContent: client ? `${client.first_name[0]}${client.last_name[0]}`.toUpperCase() : "?" }),
      el("span", {},
        el("strong", { textContent: client ? records.fullName(client) : "Unknown patient" }),
        el("span", { className: "muted small", textContent: client ? ` · ${age} years old at the visit · ${sexLabel(client.sex)} · Purok ${v.purok ?? client.purok}` : "" }),
      ),
      el("span", { className: "record-patient-go", textContent: "Patient record →" }),
    );
    $("visit-title").textContent = diagnosisLabel(v);
    $("visit-chips").replaceChildren(
      ...[
        disease?.tracked ? el("span", { className: "chip", dataset: { status: v.case_status }, textContent: STATUS_LABELS[v.case_status] }) : el("span", { className: "chip", textContent: "Not a surveillance case" }),
        urgent ? el("span", { className: "chip chip-urgent", textContent: "Urgent disease" }) : null,
        wasCorrected(v) ? el("span", { className: "chip", textContent: "Corrected" }) : null,
        v.voided ? el("span", { className: "chip", textContent: "Voided" }) : null,
      ].filter(Boolean),
    );
    $("visit-when").textContent = `Consultation on ${formatDate(v.visit_date)} · ${relativeDay(v.visit_date)}${v.attended_by ? ` · attended by ${v.attended_by}` : ""}`;

    // What matters about this record right now.
    const [syncText, syncLevel] = syncLabel(v);
    const voidEntry = v.voided ? v.history.findLast((entry) => entry.action === "VOIDED") : null;
    const lines = [];
    if (voidEntry) lines.push(statusLine("tracked", "Voided", `${voidEntry.reason}. Kept here, but no longer counted by the RHU.`));
    if (v.sync_detail) lines.push(statusLine("tracked", "The RHU did not count this report", v.sync_detail));
    if (urgent && !v.voided) lines.push(statusLine("urgent", "Urgent disease", "a single case raises an alert at the RHU and the neighboring barangays"));
    if (!voidEntry && !v.sync_detail) {
      lines.push(statusLine(syncLevel === "OK" ? "ok" : "follow-up", syncText,
        syncLevel === "OK" ? "the RHU stored the de-identified report" : "it will be sent automatically when the RHU can be reached"));
    }
    $("visit-status").replaceChildren(...lines);
    $("visit-correct").href = `#/visit-form/${v.client_id}/${v.consultation_id}`;
    $("visit-actions").hidden = v.voided;

    // The consultation, told in order.
    const notRecorded = () => el("p", { className: "muted small record-none", textContent: "Not recorded" });
    const notes = (rows) => {
      const shown = rows.filter(([, value]) => value);
      return shown.length
        ? el("dl", { className: "timeline-notes" }, ...shown.flatMap(([label, value]) => [el("dt", { textContent: label }), el("dd", { textContent: value })]))
        : notRecorded();
    };
    const onsetDays = v.onset_date ? daysBetween(v.onset_date, v.visit_date) : null;
    const vitals = vitalsOf(v, client?.birthdate);
    const flagged = vitals.filter((chip) => chip.classList.contains("vital-high")).length;
    const followUp = v.follow_up_date ? `${formatDate(v.follow_up_date)} (${relativeDay(v.follow_up_date).toLowerCase()})` : "";
    const step = (number, title, ...body) =>
      el("li", { className: "record-step" },
        el("span", { className: "record-step-number", "aria-hidden": "true", textContent: number }),
        el("div", { className: "record-step-body" }, el("h3", { textContent: title }), ...body));

    $("visit-details").replaceChildren(
      step(1, "What they came in with",
        v.chief_complaint ? el("blockquote", { className: "timeline-complaint", textContent: `“${v.chief_complaint}”` }) : notRecorded(),
        onsetDays != null
          ? el("p", { className: "muted small record-line", textContent: onsetDays === 0
            ? `Symptoms started the same day (${formatDate(v.onset_date)}).`
            : `Symptoms started ${formatDate(v.onset_date)}, ${onsetDays} day${onsetDays === 1 ? "" : "s"} before the visit.` })
          : null),
      step(2, "What was measured",
        vitals.length ? el("div", { className: "vital-chips record-vitals" }, ...vitals) : notRecorded(),
        flagged ? el("p", { className: "small record-flag", textContent: `${flagged} reading${flagged === 1 ? " is" : "s are"} outside the normal range (highlighted).` }) : null),
      step(3, "What was found",
        notes([["Findings", v.findings], ["Diagnosis", diagnosisLabel(v)],
          ["Case status", disease?.tracked ? `${STATUS_LABELS[v.case_status]}, counted in the RHU's outbreak surveillance` : "Not a surveillance case: counted as an other consultation"]])),
      step(4, "What was done",
        notes([["Treatment", v.treatment], ["Disposition", v.disposition], ["Follow-up", followUp], ["Attended by", v.attended_by]])),
    );

    // The de-identified report, exactly the fields that went to the RHU.
    const report = [
      ["Disease", disease ? (v.diagnosis_code === OTHER_CODE ? "Other consultation" : disease.name) : diagnosisLabel(v)],
      ["Case status", disease?.tracked ? STATUS_LABELS[v.case_status] : "Not applicable"],
      ["Consultation date", formatDate(v.visit_date)],
      ["Symptom onset", formatDate(v.onset_date || v.visit_date)],
      ["Age", age != null ? `${age} years` : "–"],
      ["Sex", client ? sexLabel(client.sex) : "–"],
      ["Purok", String(v.purok ?? client?.purok ?? "–")],
      ["Report status", syncText],
    ];
    $("visit-report").replaceChildren(...report.flatMap(([label, value]) => [el("dt", { textContent: label }), el("dd", { textContent: value })]));

    const history = changeHistory(v);
    $("visit-history-card").hidden = !history.length;
    $("visit-history").replaceChildren(...history);
  }

  /** Who changed what, newest first, as timeline entries. Kept on this device; only the corrected report goes to the RHU. */
  function changeHistory(v) {
    const titles = { CORRECTED: "Corrected", VOIDED: "Voided", CLIENT_CORRECTED: "Report re-sent" };
    const kinds = { CORRECTED: "other", VOIDED: "tracked", CLIENT_CORRECTED: "other" };
    return [...(v.history ?? [])].reverse().map((entry) =>
      el("li", { className: "timeline-item", dataset: { kind: kinds[entry.action] ?? "other" } },
        el("span", { className: "timeline-dot", "aria-hidden": "true" }),
        el("article", { className: "timeline-card" },
          el("div", { className: "timeline-when" },
            el("strong", { textContent: titles[entry.action] ?? entry.action }),
            el("span", { className: "muted small", textContent: new Date(entry.at).toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }) }),
          ),
          el("p", { className: "small record-line", textContent: `Reason: ${entry.reason}` }),
          ...(entry.changes ?? []).map((c) =>
            el("p", { className: "small record-change" },
              el("span", { className: "muted", textContent: `${c.field}: ` }),
              el("s", { textContent: c.from || "—" }), " → ", el("strong", { textContent: c.to || "—" }))),
        ),
      ));
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
