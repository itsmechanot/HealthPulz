// Calls to the RHU server. The page is served by the RHU itself, so paths are relative.

export async function getConfig() {
  const res = await fetch("/api/config");
  if (!res.ok) throw new Error(`Could not load config (HTTP ${res.status})`);
  return res.json();
}

export async function registerNode(nodeId, barangayId) {
  const res = await fetch("/api/nodes/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "REGISTER", node_id: nodeId, barangay_id: barangayId }),
  });
  if (!res.ok) throw new Error(`Registration failed (HTTP ${res.status})`);
  return res.json();
}

/** Throws only on network failure; HTTP errors are returned so the caller can react to each code. */
export async function submitBatch(batch) {
  const res = await fetch("/api/batches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(batch),
  });
  return {
    status: res.status,
    retryAfter: Number(res.headers.get("Retry-After") || 2),
    body: await res.json().catch(() => null),
  };
}

export async function getBatchStatus(batchId) {
  const res = await fetch(`/api/batches/${batchId}`);
  if (!res.ok) throw new Error(`Status check failed (HTTP ${res.status})`);
  return res.json();
}

export function wsUrl(path) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}${path}`;
}
