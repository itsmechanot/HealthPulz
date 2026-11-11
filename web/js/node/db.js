// IndexedDB: the Demo Tools outbox, and old browser records that are only read to move them.

import { toast } from "../common/shell.js";

const DB_NAME = "healthpulz-node";
const DB_VERSION = 2;

let dbPromise = null;

/** Resolves to the database, or null if this browser can't store data (e.g. some private windows). */
export function openDb() {
  dbPromise ??= new Promise((resolve) => {
    try {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = (event) => {
        const db = request.result;
        if (event.oldVersion < 1) db.createObjectStore("outbox", { keyPath: "batch_id" });
        if (event.oldVersion < 2) {
          db.createObjectStore("clients", { keyPath: "client_id" }).createIndex("barangay_id", "barangay_id");
          const consultations = db.createObjectStore("consultations", { keyPath: "consultation_id" });
          consultations.createIndex("barangay_id", "barangay_id");
          consultations.createIndex("client_id", "client_id");
          consultations.createIndex("batch_id", "batch_id");
        }
      };
      request.onblocked = () =>
        toast({ title: "Update waiting", body: "Close other HealthPulz tabs on this device, then reload.", durationMs: 15000 });
      request.onsuccess = () => {
        request.result.onversionchange = () => request.result.close();
        resolve(request.result);
      };
      request.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return dbPromise;
}

/** Runs work(stores) in one transaction: all of it is saved, or none. */
export async function transaction(storeNames, mode, work) {
  const db = await openDb();
  if (!db) throw new Error("This browser cannot store data on this device.");
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeNames, mode);
    const stores = Object.fromEntries(storeNames.map((name) => [name, tx.objectStore(name)]));
    const outcome = work(stores);
    tx.oncomplete = () => resolve(outcome instanceof IDBRequest ? outcome.result : outcome);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

/** Records this browser still holds for a barangay. Empty if it can't store data. */
export async function browserRecords(barangayId) {
  if (!(await openDb())) return { clients: [], consultations: [] };
  const [clients, consultations] = await Promise.all(
    ["clients", "consultations"].map((name) =>
      transaction([name], "readonly", (stores) => stores[name].index("barangay_id").getAll(barangayId)),
    ),
  );
  return { clients, consultations };
}

export async function clearBrowserRecords(barangayId) {
  const { clients, consultations } = await browserRecords(barangayId);
  await transaction(["clients", "consultations"], "readwrite", (stores) => {
    clients.forEach((c) => stores.clients.delete(c.client_id));
    consultations.forEach((c) => stores.consultations.delete(c.consultation_id));
  });
}
