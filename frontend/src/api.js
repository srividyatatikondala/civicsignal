// Thin client for the CivicSignal backend. All SerpApi access happens server-side.

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d) => d.msg).join("; ");
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const getHealth = () => request("/api/health");
export const getDemoQueries = () => request("/api/demo-queries");
export const listInvestigations = () => request("/api/investigations?limit=8");
export const getReport = (id) => request(`/api/investigations/${encodeURIComponent(id)}/report`);

export async function investigate(query) {
  const inv = await request("/api/investigate", {
    method: "POST",
    body: JSON.stringify({ query }),
  });
  return getReport(inv.id);
}
