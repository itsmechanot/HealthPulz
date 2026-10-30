// ID helpers.
//
// crypto.randomUUID() only exists on HTTPS pages or localhost. Classmates open
// the node page over plain HTTP on the LAN (e.g. http://192.168.1.5:8000), so
// UUIDs are built from crypto.getRandomValues(), which works everywhere.

export function uuid4() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

const SUFFIX_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

/** 'BRGY-08' -> 'BRGY-08-K7QX' (format required by the protocol). */
export function newNodeId(barangayId) {
  const bytes = crypto.getRandomValues(new Uint8Array(4));
  const suffix = [...bytes].map((b) => SUFFIX_CHARS[b % SUFFIX_CHARS.length]).join("");
  return `${barangayId}-${suffix}`;
}
