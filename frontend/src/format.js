// Shared formatting + presentation helpers.

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const INR_CENTS = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const INT = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function formatINR(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return INR.format(Number(value));
}

export function formatINRWithCents(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return INR_CENTS.format(Number(value));
}

export function formatInt(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return INT.format(Number(value));
}

export function formatPct(value, fractionDigits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(fractionDigits)}%`;
}

export function formatPctSigned(value, fractionDigits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const n = Number(value) * 100;
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return `${sign}${Math.abs(n).toFixed(fractionDigits)}%`;
}

export function formatRelativeTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diff = (Date.now() - then) / 1000;
  if (Math.abs(diff) < 60) return "just now";
  const mins = Math.round(diff / 60);
  if (Math.abs(mins) < 60) return `${Math.abs(mins)} min ago`;
  const hours = Math.round(mins / 60);
  if (Math.abs(hours) < 24) return `${Math.abs(hours)} hr ago`;
  const days = Math.round(hours / 24);
  return `${Math.abs(days)} day${Math.abs(days) === 1 ? "" : "s"} ago`;
}

const STRATEGY_LABELS = {
  retry_now: "Retry now",
  retry_later: "Retry later",
  payment_link: "Send payment link",
  alternate_payment_method: "Alternate payment method",
  customer_reminder: "Customer reminder",
  human_escalation: "Human escalation",
  stop: "Stop",
};

export function strategyLabel(name) {
  if (!name) return "—";
  return STRATEGY_LABELS[name] ?? name.replace(/_/g, " ");
}

const FAILURE_LABELS = {
  temporary_network: "Temporary / network",
  temporary_failure: "Temporary / network",
  insufficient_funds: "Insufficient funds",
  expired_card: "Expired card",
  recurring_mandate_failure: "Recurring mandate failure",
  permanent_decline: "Permanent decline",
  suspected_fraud: "Suspected fraud",
  unknown: "Unknown",
};

export function failureLabel(name) {
  if (!name) return "—";
  return FAILURE_LABELS[name] ?? name.replace(/_/g, " ");
}

// Maps an execution status string to a chip variant + label.
const STATUS_META = {
  simulated_recovered: { label: "Recovered", variant: "success" },
  simulated_no_recovery: { label: "Not recovered", variant: "warn" },
  simulated_human_review_queued: { label: "Human review", variant: "escalation" },
  no_action: { label: "Stopped", variant: "stopped" },
  blocked: { label: "Blocked", variant: "blocked" },
};

export function statusMeta(status) {
  if (!status) return { label: "—", variant: "neutral" };
  return STATUS_META[status] ?? { label: status.replace(/_/g, " "), variant: "neutral" };
}

export function riskVariant(level) {
  switch ((level ?? "").toLowerCase()) {
    case "low": return "success";
    case "medium": return "warn";
    case "high": return "danger";
    default: return "neutral";
  }
}

export function categoryVariant(category) {
  switch (category) {
    case "insufficient_funds": return "warn";
    case "expired_card": return "blocked";
    case "permanent_decline": return "blocked";
    case "suspected_fraud": return "danger";
    case "recurring_mandate_failure": return "neutral";
    case "temporary_network":
    case "temporary_failure": return "neutral";
    default: return "neutral";
  }
}
