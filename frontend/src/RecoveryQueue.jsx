import { useEffect, useState } from "react";
import { Icon } from "./Icon.jsx";
import { api, REPRESENTATIVE_PAYMENT_IDS, DEMO_PAYMENT_META } from "./api.js";
import {
  categoryVariant,
  failureLabel,
  formatINRWithCents,
  formatPct,
  riskVariant,
  statusMeta,
  strategyLabel,
} from "./format.js";

function Chip({ variant = "neutral", children }) {
  return (
    <span className={`chip ${variant}`}>
      <span className="dot" />
      {children}
    </span>
  );
}

function Spinner({ label = "Loading recovery queue…" }) {
  return (
    <div className="state">
      <div className="spinner" />
      <strong>{label}</strong>
      <span>Fetching the six representative failed payments from the backend.</span>
    </div>
  );
}

function ErrorState({ error, onRetry }) {
  return (
    <div className="state">
      <div className="icon"><Icon name="alert" size={18} /></div>
      <strong>Couldn't load the recovery queue</strong>
      <span>{String(error?.message ?? error)}</span>
      {onRetry ? (
        <button className="back-link" onClick={onRetry}>Retry</button>
      ) : null}
    </div>
  );
}

const METHOD_LABELS = {
  card: "Card",
  upi: "UPI",
  netbanking: "Netbanking",
};

function methodLabel(method) {
  if (!method) return "—";
  return METHOD_LABELS[method] ?? method;
}

function riskLabel(level) {
  if (!level) return "—";
  return level.charAt(0).toUpperCase() + level.slice(1);
}

export default function RecoveryQueue({ onSelectPayment }) {
  const [state, setState] = useState({ status: "loading", cases: [], error: null });

  const load = async () => {
    setState({ status: "loading", cases: [], error: null });
    try {
      const cases = await Promise.all(
        REPRESENTATIVE_PAYMENT_IDS.map(async (id) => {
          try {
            // Read-only inspection of any existing recovery decisions.
            const decision = await api.getDecision(id);
            return { paymentId: id, decision, error: null };
          } catch (err) {
            return { paymentId: id, decision: null, error: err };
          }
        })
      );
      const allFailed = cases.every((c) => c.error);
      if (allFailed) {
        throw cases[0].error;
      }
      setState({ status: "ready", cases, error: null });
    } catch (err) {
      setState({ status: "error", cases: [], error: err });
    }
  };

  useEffect(() => { load(); }, []);

  if (state.status === "loading") return <Spinner />;
  if (state.status === "error") return <ErrorState error={state.error} onRetry={load} />;

  const ready = state.cases.filter((c) => c.decision);

  return (
    <section className="cases-table">
      <div className="cases-header">
        <div>
          <h3><span className="icon-badge"><Icon name="queue" size={14} /></span> Recovery queue</h3>
          <p>
            Six failed payments from the backend demo dataset, enriched with display-only metadata.
            Click any row to see the full AI decision — diagnosis, simulation, guardrails, and execution.
          </p>
        </div>
        <div className="head-meta">Backend demo dataset · {ready.length} of {REPRESENTATIVE_PAYMENT_IDS.length} loaded</div>
      </div>
      {ready.length === 0 ? (
        <div className="state" style={{ padding: "36px 16px" }}>
          <strong>No demo payments available</strong>
          <span>Ensure the backend's demo dataset is seeded.</span>
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Payment</th>
              <th>Customer</th>
              <th>Amount</th>
              <th>Method</th>
              <th>Failure</th>
              <th>Risk</th>
              <th>Selected strategy</th>
              <th>Expected recovery</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {ready.map((c) => {
              const decision = c.decision;
              const meta = DEMO_PAYMENT_META[c.paymentId] ?? {};
              const diagnosis = decision?.diagnosis ?? {};
              const selected = decision?.selected_strategy;
              const ranked = decision?.optimization?.ranked_strategies ?? [];
              const winner = ranked.find((r) => r.strategy === selected) ?? ranked[0];
              const expected = winner?.expected_recovered_amount ?? decision?.expected_recovered_amount;
              const status = statusMeta(decision?.execution?.simulated_outcome);
              const customerId = diagnosis?.customer_id ?? meta.customerId ?? "—";
              return (
                <tr key={c.paymentId} onClick={() => onSelectPayment(c.paymentId, decision)}>
                  <td className="id">{c.paymentId}</td>
                  <td style={{ color: "var(--ink-700)", fontSize: 12.5 }}>{customerId}</td>
                  <td className="amount">{formatINRWithCents(meta.amount)}</td>
                  <td>{methodLabel(meta.paymentMethod)}</td>
                  <td>
                    <Chip variant={categoryVariant(diagnosis.failure_category)}>
                      {failureLabel(diagnosis.failure_category)}
                    </Chip>
                  </td>
                  <td>
                    <Chip variant={riskVariant(meta.riskLevel)}>
                      {riskLabel(meta.riskLevel)}
                    </Chip>
                  </td>
                  <td>{strategyLabel(selected)}</td>
                  <td className="amount">{formatINRWithCents(expected)}</td>
                  <td>
                    <Chip variant={status.variant}>{status.label}</Chip>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
