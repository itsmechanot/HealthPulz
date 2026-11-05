// The node's outbox: batches waiting for a STORED confirmation from the RHU.
//
// Stored in IndexedDB so unsent batches survive a page reload or a dropped
// connection. If IndexedDB is unavailable (e.g. some private windows), it
// falls back to memory so the page still works.

import { openDb, transaction } from "./db.js";

const memory = new Map();

const run = (mode, work) => transaction(["outbox"], mode, ({ outbox }) => work(outbox));

export async function addBatches(batches) {
  if (!(await openDb())) {
    batches.forEach((b) => memory.set(b.batch_id, b));
    return;
  }
  await run("readwrite", (store) => batches.forEach((b) => store.put(b)));
}

/** All waiting batches, oldest first. */
export async function allBatches() {
  const batches = (await openDb()) ? await run("readonly", (store) => store.getAll()) : [...memory.values()];
  return batches.sort((a, b) => a.sent_at.localeCompare(b.sent_at));
}

export async function removeBatch(batchId) {
  if (!(await openDb())) {
    memory.delete(batchId);
    return;
  }
  await run("readwrite", (store) => store.delete(batchId));
}

export async function hasBatch(batchId) {
  if (!(await openDb())) return memory.has(batchId);
  return (await run("readonly", (store) => store.count(batchId))) > 0;
}

export async function countBatches() {
  return (await openDb()) ? run("readonly", (store) => store.count()) : memory.size;
}
