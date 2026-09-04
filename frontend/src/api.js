// Thin client around the PayFix FastAPI backend.
// All requests go to the same origin so Vite's dev-server proxy is the only
// thing that needs to know about the host/port.

const BASE = import.meta.env.VITE_API_BASE ?? "";

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? body.message ?? JSON.stringify(body);
    } catch {
      /* ignore parse errors and fall back to statusText */
    }
    throw new Error(`${response.status} ${detail}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

// Build a query string while skipping null/undefined values. Booleans and
// numbers are stringified; arrays are repeated as `?key=a&key=b` which is the
// shape FastAPI's `list[str]` query parameter expects.
function buildQuery(params) {
  if (!params) return "";
  const entries = [];
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) {
      for (const v of value) entries.push([key, String(v)]);
    } else {
      entries.push([key, String(value)]);
    }
  }
  if (!entries.length) return "";
  const qs = new URLSearchParams(entries).toString();
  return `?${qs}`;
}

export const api = {
  health() {
    return request("/health");
  },

  // /evaluation/run exposes its parameters as query strings, not a JSON body.
  // Sending a JSON body causes FastAPI to reject the request with a 422.
  runEvaluation({ batchSize = 100, paymentIds } = {}) {
    const query = buildQuery({
      batch_size: batchSize,
      payment_ids: paymentIds,
    });
    return request(`/evaluation/run${query}`, { method: "POST" });
  },

  diagnose(paymentId) {
    return request(`/payments/${encodeURIComponent(paymentId)}/diagnose`, {
      method: "POST",
    });
  },

  optimize(paymentId) {
    return request(`/payments/${encodeURIComponent(paymentId)}/optimize`, {
      method: "POST",
    });
  },

  recover(paymentId) {
    return request(`/payments/${encodeURIComponent(paymentId)}/recover`, {
      method: "POST",
    });
  },
};

// Canonical list of "representative" recovery-case payment IDs.
// These six IDs are part of the existing backend demo dataset and are used
// in the dashboard only to illustrate the AI decision flow on real
// response payloads — they are NOT the full batch.
export const REPRESENTATIVE_PAYMENT_IDS = [
  "pay_demo_network",
  "pay_demo_funds",
  "pay_demo_expired",
  "pay_demo_decline",
  "pay_demo_fraud",
  "pay_demo_subscription",
];
