// This BHU's client and checkup records, kept on the server in the barangay's own store
// (rhu/bhu_store.py), so they are the same on any device or address. The server turns each
// checkup into a de-identified report for the RHU; names and clinical details stay in the store.

import { request as loggedInFetch } from "../common/api.js";
import { uuid4 } from "../common/ids.js";

export const SYNC = { WAITING: "WAITING", STORED: "STORED", REJECTED: "REJECTED" };

let base = null;

export function useBarangay(barangayId) {
  base = `/api/bhu/${barangayId}`;
}

/** Throws an Error whose message can be shown to the BHU worker. */
async function request(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await loggedInFetch(base + path, {
      method,
      ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    });
  } catch {
    throw new Error("Cannot reach the RHU server. Nothing was saved. Check the Wi-Fi and try again.");
  }
  if (res.ok) return res.json();
  const detail = (await res.json().catch(() => null))?.detail;
  const error = new Error(typeof detail === "string" ? detail : "The server refused these details. Please check the form.");
  error.status = res.status;
  throw error;
}

const orNull = (promise) => promise.catch((error) => {
  if (error.status === 404) return null;
  throw error;
});

// ---- helpers ------------------------------------------------------------------------

/** Completed years between two YYYY-MM-DD dates. */
export function ageOn(birthdate, date) {
  const [by, bm, bd] = birthdate.split("-").map(Number);
  const [y, m, d] = date.split("-").map(Number);
  return y - by - (m < bm || (m === bm && d < bd) ? 1 : 0);
}

export const fullName = (c) => `${c.last_name}, ${c.first_name}${c.middle_name ? ` ${c.middle_name}` : ""}`;

// ---- clients ----------------------------------------------------------------------------

export const listClients = () => request("/clients");
export const getClient = (clientId) => orNull(request(`/clients/${clientId}`));

/** The caller keeps `clientId` across retries, so a retry can't register the client twice. */
export const saveClient = (clientId, fields) => request(`/clients/${clientId}`, { method: "PUT", body: fields });

// ---- checkups -----------------------------------------------------------------------------

export const newId = uuid4;
export const listConsultations = () => request("/consultations");
export const listClientConsultations = (clientId) => request(`/consultations?client_id=${clientId}`);
export const getConsultation = (id) => orNull(request(`/consultations/${id}`));

/** The caller keeps `consultationId` across retries, so a retry can't record the checkup twice. */
export const saveConsultation = (clientId, consultationId, fields) =>
  request(`/clients/${clientId}/consultations/${consultationId}`, { method: "PUT", body: fields });

export const correctConsultation = (id, fields, reason, changes) =>
  request(`/consultations/${id}/correct`, { method: "POST", body: { fields, reason, changes } });

export const voidConsultation = (id, reason) => request(`/consultations/${id}/void`, { method: "POST", body: { reason } });

// ---- backup ---------------------------------------------------------------------------------

export const exportBackup = () => request("/backup");
export const importBackup = (backup) => request("/backup", { method: "POST", body: backup });
