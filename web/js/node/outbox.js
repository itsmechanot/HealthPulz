// Demo Tools outbox: batches waiting for STORED, in IndexedDB (or memory) so they survive a reload.

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

/** This station's waiting batches, oldest first. A browser shared by two BHUs keeps each one's apart. */
export async function allBatches(nodeId) {
  const batches = (await openDb()) ? await run("readonly", (store) => store.getAll()) : [...memory.values()];
  return batches.filter((b) => b.node_id === nodeId).sort((a, b) => a.sent_at.localeCompare(b.sent_at));
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
