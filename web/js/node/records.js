// Client and consultation records, stored only on this device.
//
// The one thing that leaves the device is the case report built by toCaseRecord():
// disease, dates, age, sex, purok and status. Names, birthdates, contact details,
// vital signs, findings and treatment stay here (Data Privacy Act of 2012).

import { uuid4 } from "../common/ids.js";
import { openDb, transaction } from "./db.js";

const PROTOCOL_VERSION = 1;
const BACKUP_FORMAT = "healthpulz-bhu-backup";

export const SYNC = { WAITING: "WAITING", STORED: "STORED", REJECTED: "REJECTED" };

export const isAvailable = async () => (await openDb()) !== null;

// ---- pure helpers -----------------------------------------------------------------

/** Completed years between two YYYY-MM-DD dates. */
export function ageOn(birthdate, date) {
  const [by, bm, bd] = birthdate.split("-").map(Number);
  const [y, m, d] = date.split("-").map(Number);
  return y - by - (m < bm || (m === bm && d < bd) ? 1 : 0);
}

export const fullName = (c) => `${c.last_name}, ${c.first_name}${c.middle_name ? ` ${c.middle_name}` : ""}`;

/** What the RHU receives for a consultation. Nothing here identifies the client. */
export function toCaseRecord(client, consultation) {
  return {
    record_id: consultation.consultation_id,
    disease_code: consultation.diagnosis_code,
    onset_date: consultation.onset_date || consultation.visit_date,
    consult_date: consultation.visit_date,
    age_years: ageOn(client.birthdate, consultation.visit_date),
    sex: client.sex,
    purok: client.purok ?? null,
    case_status: consultation.case_status,
  };
}

function toBatch(nodeId, batchId, record) {
  return {
    type: "SUBMIT_BATCH",
    protocol_version: PROTOCOL_VERSION,
    node_id: nodeId,
    batch_id: batchId,
    sent_at: new Date().toISOString(),
    records: [record],
    drill: false,
  };
}

// ---- clients ---------------------------------------------------------------------

export async function saveClient(client) {
  const now = new Date().toISOString();
  const saved = { client_id: uuid4(), created_at: now, ...client, updated_at: now };
  await transaction(["clients"], "readwrite", ({ clients }) => clients.put(saved));
  return saved;
}

export const getClient = (clientId) => transaction(["clients"], "readonly", ({ clients }) => clients.get(clientId));

export const listClients = (barangayId) =>
  transaction(["clients"], "readonly", ({ clients }) => clients.index("barangay_id").getAll(barangayId));

// ---- consultations -----------------------------------------------------------------

/**
 * Saves a consultation and queues its case report for the RHU in one transaction,
 * so a record can never be saved without being sent, or sent without being saved.
 */
export async function saveConsultation(nodeId, client, fields) {
  const consultation = {
    ...fields,
    consultation_id: uuid4(),
    client_id: client.client_id,
    barangay_id: client.barangay_id,
    batch_id: uuid4(),
    sync: SYNC.WAITING,
    sync_detail: null,
    created_at: new Date().toISOString(),
  };
  const batch = toBatch(nodeId, consultation.batch_id, toCaseRecord(client, consultation));
  await transaction(["consultations", "outbox"], "readwrite", ({ consultations, outbox }) => {
    consultations.put(consultation);
    outbox.put(batch);
  });
  return consultation;
}

export const getConsultation = (id) =>
  transaction(["consultations"], "readonly", ({ consultations }) => consultations.get(id));

const newestFirst = (a, b) => b.visit_date.localeCompare(a.visit_date) || b.created_at.localeCompare(a.created_at);

export async function listConsultations(barangayId) {
  const all = await transaction(["consultations"], "readonly", ({ consultations }) =>
    consultations.index("barangay_id").getAll(barangayId),
  );
  return all.sort(newestFirst);
}

export async function listClientConsultations(clientId) {
  const all = await transaction(["consultations"], "readonly", ({ consultations }) =>
    consultations.index("client_id").getAll(clientId),
  );
  return all.sort(newestFirst);
}

/** Records what the RHU answered for a batch. Other batches (demo tools) match nothing. */
export async function markDelivery(batchId, sync, detail = null) {
  let updated = false;
  await transaction(["consultations"], "readwrite", ({ consultations }) => {
    const request = consultations.index("batch_id").get(batchId);
    request.onsuccess = () => {
      if (!request.result) return;
      consultations.put({ ...request.result, sync, sync_detail: detail });
      updated = true;
    };
  });
  return updated;
}

// ---- backup ---------------------------------------------------------------------------

export async function exportBackup(barangayId) {
  const [clients, consultations] = await Promise.all([listClients(barangayId), listConsultations(barangayId)]);
  return { format: BACKUP_FORMAT, version: 1, barangay_id: barangayId, exported_at: new Date().toISOString(), clients, consultations };
}

/**
 * Restores a backup into this device. Existing records with the same IDs are overwritten.
 * Reports that hadn't been confirmed are queued again; resending is safe because the RHU
 * ignores record IDs it already has. Throws if the file isn't a backup of this barangay.
 */
export async function importBackup(nodeId, barangayId, backup) {
  if (backup?.format !== BACKUP_FORMAT || !Array.isArray(backup.clients) || !Array.isArray(backup.consultations)) {
    throw new Error("This file is not a HealthPulz backup.");
  }
  if (backup.barangay_id !== barangayId) {
    throw new Error("This backup belongs to a different barangay.");
  }
  const clientsById = new Map(backup.clients.map((c) => [c.client_id, c]));
  await transaction(["clients", "consultations", "outbox"], "readwrite", ({ clients, consultations, outbox }) => {
    backup.clients.forEach((c) => clients.put(c));
    for (const c of backup.consultations) {
      consultations.put(c);
      const client = clientsById.get(c.client_id);
      if (c.sync === SYNC.WAITING && client) outbox.put(toBatch(nodeId, c.batch_id, toCaseRecord(client, c)));
    }
  });
  return { clients: backup.clients.length, consultations: backup.consultations.length };
}
