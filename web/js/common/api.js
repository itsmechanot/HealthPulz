// Calls to the RHU server. The page is served by the RHU itself, so paths are relative.

// WebSocket close code: not logged in.
export const CLOSE_NOT_LOGGED_IN = 4401;

export function toLogin() {
  location.replace("/");
}

/** fetch for logged-in calls; on 401 it goes to the login page and never resolves. */
export async function request(url, options) {
  const res = await fetch(url, options);
  if (res.status !== 401) return res;
  toLogin();
  return new Promise(() => {});
}

export async function getAccount() {
  const res = await request("/api/auth/me");
  if (!res.ok) throw new Error(`Could not load the account (HTTP ${res.status})`);
  return res.json();
}

export async function logOut() {
  await fetch("/api/auth/logout", { method: "POST" });
  toLogin();
}

/** fetch + JSON; throws on network errors and non-2xx responses. */
export async function fetchJson(url, options) {
  const res = await request(url, options);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

export function trendUrl(disease, barangayId, weeks) {
  const params = new URLSearchParams({ disease, weeks });
  if (barangayId) params.set("barangay_id", barangayId);
  return `/api/stats/trend?${params}`;
}

export async function getConfig() {
  const res = await fetch("/api/config");
  if (!res.ok) throw new Error(`Could not load config (HTTP ${res.status})`);
  return res.json();
}

export async function registerNode(nodeId, barangayId) {
  const res = await request("/api/nodes/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "REGISTER", node_id: nodeId, barangay_id: barangayId }),
  });
  if (!res.ok) throw new Error(`Registration failed (HTTP ${res.status})`);
  return res.json();
}

/** Throws only on network failure; HTTP errors are returned so the caller can react to each code. */
export async function submitBatch(batch) {
  const res = await request("/api/batches", {
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
  const res = await request(`/api/batches/${batchId}`);
  if (!res.ok) throw new Error(`Status check failed (HTTP ${res.status})`);
  return res.json();
}

export function wsUrl(path) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}${path}`;
}
