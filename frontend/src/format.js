// Display-only formatting. The data from the API is never changed; only how it reads on screen.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function lastDay(y, m) {
  return new Date(Date.UTC(y, m, 0)).getUTCDate(); // m is 1-based
}

function one(y, m, d) {
  return `${Number(d)} ${MONTHS[m - 1]} ${y}`;
}

function range(a, b) {
  const [y1, m1, d1] = a.split("-").map(Number);
  const [y2, m2, d2] = b.split("-").map(Number);
  if (d1 === 1 && d2 === lastDay(y2, m2)) {
    if (y1 === y2 && m1 === m2) return `${MONTHS[m1 - 1]} ${y1}`;
    return y1 === y2 ? `${MONTHS[m1 - 1]}–${MONTHS[m2 - 1]} ${y2}` : `${MONTHS[m1 - 1]} ${y1}–${MONTHS[m2 - 1]} ${y2}`;
  }
  return `${one(y1, m1, d1)} – ${one(y2, m2, d2)}`;
}

// "2026-07-01/2026-07-31" -> "Jul 2026"; "2026-06-20" -> "20 Jun 2026". Leaves other text untouched.
export function readable(text) {
  if (!text) return text;
  return String(text)
    .replace(/(\d{4}-\d{2}-\d{2})\/(\d{4}-\d{2}-\d{2})/g, (_, a, b) => range(a, b))
    .replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (_, y, m, d) => one(+y, +m, d));
}

export const STATUS_SHORT = {
  CONFLICTING: "Conflicting claims",
  POTENTIALLY_STALE: "Potentially stale",
  INSUFFICIENT_EVIDENCE: "Insufficient evidence",
  MINOR_ISSUES: "Minor issues",
  NOT_ASSESSED: "No issues detected",
  MIXED_EVIDENCE: "Mixed evidence",
  CLEAR: "No issues detected",
};

export const STATUS_TONE = {
  CONFLICTING: "danger",
  POTENTIALLY_STALE: "warn",
  INSUFFICIENT_EVIDENCE: "muted",
  MINOR_ISSUES: "info",
  NOT_ASSESSED: "ok",
  MIXED_EVIDENCE: "warn",
  CLEAR: "ok",
};

export const STATUS_ICON = {
  CONFLICTING: "conflict",
  POTENTIALLY_STALE: "clock",
  INSUFFICIENT_EVIDENCE: "info",
  MINOR_ISSUES: "info",
  NOT_ASSESSED: "check",
  MIXED_EVIDENCE: "alert",
  CLEAR: "check",
};
