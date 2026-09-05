import { useEffect, useMemo, useState } from "react";
import { Icon } from "./Icon.jsx";
import { api, REPRESENTATIVE_PAYMENT_IDS } from "./api.js";
import {
  categoryVariant,
  failureLabel,
  formatINRWithCents,
  formatPct,
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

function Spinner({ label = "Loading AI decisions…" }) {
  return (
    <div className="state">
      <div className="spinner" />
      <strong>{label}</strong>
      <span>Fetching persisted decision snapshots for the six representative demo payments.</span>
    </div>
  );
}

function ErrorState({ error, onRetry }) {
  return (
    <div className="state">
      <div className="icon"><Icon name="alert" size={18} /></div>
      <strong>Couldn't load AI decisions</strong>
      <span>{String(error?.message ?? error)}</span>
      {onRetry ? (
        <button className="back-link" onClick={onRetry}>Retry</button>
      ) : null}
    </div>
  );
}

function KV({ items }) {
  return (
    <dl className="kv">
      {items.map(([label, value], idx) => (
        <span key={idx} style={{ display: "contents" }}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </span>
      ))}
    </dl>
  );
}

function StrategyRow({ strategy, isWinner, isBlocked }) {
  return (
    <div className={`strategy ${isWinner ? "winner" : ""} ${isBlocked ? "blocked" : ""}`}>
      <div className="name">
        {strategyLabel(strategy.strategy)}
        {isWinner ? <span className="winner-badge">Selected</span> : null}
        {isBlocked ? <Chip variant="blocked">Blocked</Chip> : null}
        {!strategy.eligible && !isBlocked ? <Chip variant="neutral">Ineligible</Chip> : null}
      </div>
      <div className="metrics">
        <span>Expected: <strong>{formatINRWithCents(strategy.expected_recovered_amount)}</strong></span>
        <span>Success: <strong>{formatPct(strategy.success_probability, 0)}</strong></span>
      </div>
      {strategy.rationale ? (
        <div className="rationale">{strategy.rationale}</div>
      ) : null}
    </div>
  );
}

function GuardrailChecks({ checks }) {
  if (!checks.length) {
    return (
      <div className="state" style={{ padding: "16px 12px" }}>
        <strong>No guardrail details returned</strong>
        <span>Guardrail context was not preserved for this decision.</span>
      </div>
    );
  }
  return (
    <div className="checks">
      {checks.map((c, i) => (
        <div key={i} className={`check ${c.passed ? "pass" : "fail"}`}>
          <span className="mark">{c.passed ? <Icon name="check" size={11} strokeWidth={2.4} /> : <Icon name="x" size={11} strokeWidth={2.4} />}</span>
          <div>
            <div><strong>{c.label}</strong></div>
            {c.detail ? <div style={{ color: "var(--ink-500)", marginTop: 2, fontSize: 12.5 }}>{c.detail}</div> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

// Builds a small list of generic guardrail checks from the decision payload
// the way PaymentDetail does. Used by the expanded detail panel.
function buildChecks({ decision }) {
  const execution = decision?.execution;
  const guardrail = decision?.guardrail_result;
  const executionAllowed = !!guardrail?.allowed && execution?.simulated_outcome !== "blocked";
  const items = [];
  items.push({
    label: "Strategy eligible",
    passed: !!guardrail,
    detail: guardrail
      ? `${strategyLabel(guardrail.strategy)} was considered by the engine.`
      : "No eligible strategy was found for this payment.",
  });
  items.push({
    label: "Guardrail decision",
    passed: !!guardrail?.allowed,
    detail: guardrail?.reason ?? "Guardrails have not yet evaluated this strategy.",
  });
  items.push({
    label: "Execution allowed",
    passed: executionAllowed,
    detail: execution?.reason ?? "No execution recorded yet.",
  });
  items.push({
    label: "Recorded in audit log",
    passed: !!execution?.timestamp,
    detail: execution?.timestamp
      ? `Executed at ${execution.timestamp}.`
      : "No audit record available.",
  });
  return items;
}

function DecisionDetail({ decision, paymentId, onCollapse }) {
  const diagnosis = decision?.diagnosis ?? {};
  const ranked = decision?.optimization?.ranked_strategies ?? [];
  const selected = decision?.selected_strategy ?? ranked[0]?.strategy;
  const execution = decision?.execution ?? {};
  const guardrail = decision?.guardrail_result ?? {};
  const meta = statusMeta(execution?.simulated_outcome);
  const checks = useMemo(() => buildChecks({ decision }), [decision]);

  return (
    <section className="detail-grid" style={{ marginTop: 18 }}>
      <div className="panel span-2">
        <div className="panel-head">
          <h3>
            <span className="icon-badge"><Icon name="money" size={14} /></span>
            Payment summary · {paymentId}
          </h3>
          <button className="back-link" onClick={onCollapse}>
            <Icon name="back" size={14} /> Collapse
          </button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18, alignItems: "start" }}>
          <div>
            <div className="exec-result">
              <div>
                <div className="small">Recovered (simulated)</div>
                <div className={`amount ${Number(execution?.recovered_amount ?? 0) > 0 ? "recovered" : ""}`}>
                  {formatINRWithCents(execution?.recovered_amount ?? 0)}
                </div>
              </div>
              <div>
                <div className="small">Expected recovery</div>
                <div className="amount">{formatINRWithCents(decision?.expected_recovered_amount ?? 0)}</div>
              </div>
            </div>
            <div style={{ marginTop: 14 }}>
              <KV items={[
                ["Failure category", failureLabel(diagnosis.failure_category)],
                ["Likely cause", diagnosis.likely_cause || "—"],
                ["Retryability", diagnosis.retryability_assessment || "—"],
                ["Diagnosis confidence", formatPct(diagnosis.confidence, 0)],
                ["Selected strategy", strategyLabel(selected)],
              ]} />
            </div>
          </div>
          <div>
            <div style={{ marginTop: 14 }}>
              <KV items={[
                ["Risk observation", diagnosis.risk_observation || "—"],
                ["Timing observation", diagnosis.timing_observation || "—"],
                ["Customer context", diagnosis.customer_context_observation || "—"],
                ["Outcome", <Chip key="outcome" variant={meta.variant}>{meta.label}</Chip>],
                ["Execution timestamp", execution?.timestamp || "—"],
              ]} />
            </div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3><span className="icon-badge"><Icon name="diagnose" size={14} /></span> AI diagnosis</h3>
          <div className="meta">Failure analysis</div>
        </div>
        <div className="explain">
          {diagnosis.explanation || "The diagnosis service did not return an explanation for this payment."}
        </div>
        <div className="confidence-bar">
          <div className="track">
            <div className="fill" style={{ width: `${Math.min(100, (Number(diagnosis.confidence ?? 0) * 100))}%` }} />
          </div>
          <div className="label">Confidence {formatPct(diagnosis.confidence, 0)}</div>
        </div>
        <div style={{ marginTop: 16 }}>
          <div className="panel-section">
            <h4>Likely cause</h4>
            <div style={{ fontSize: 14, fontWeight: 600 }}>
              {diagnosis.likely_cause || failureLabel(diagnosis.failure_category)}
            </div>
          </div>
          <div className="panel-section">
            <h4>Eligible strategies</h4>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {(diagnosis.eligible_strategy_names ?? []).length === 0 ? (
                <Chip variant="blocked">None</Chip>
              ) : (
                diagnosis.eligible_strategy_names.map((s) => (
                  <Chip key={s} variant="neutral">{strategyLabel(s)}</Chip>
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3><span className="icon-badge"><Icon name="simulate" size={14} /></span> Ranked strategy outcomes</h3>
          <div className="meta">{ranked.length} strategies evaluated</div>
        </div>
        <div className="strategy-list">
          {ranked.length === 0 ? (
            <div className="state" style={{ padding: "20px 12px" }}>
              <strong>No strategies to simulate</strong>
              <span>The eligibility engine produced no ranked strategies.</span>
            </div>
          ) : (
            ranked.map((s) => (
              <StrategyRow
                key={s.strategy}
                strategy={s}
                isWinner={s.strategy === selected}
                isBlocked={!guardrail?.allowed && s.strategy === selected}
              />
            ))
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3><span className="icon-badge"><Icon name="ai" size={14} /></span> Why PayFix chose this</h3>
          <div className="meta">AI reasoning</div>
        </div>
        <div className="reasoning">
          <div className="ai-mark"><Icon name="ai" size={14} /></div>
          <div className="body">
            {decision?.selection_reason || "No reasoning was recorded for this decision."}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3><span className="icon-badge"><Icon name="shield" size={14} /></span> Guardrails</h3>
          <div className="meta">Deterministic safety checks</div>
        </div>
        <GuardrailChecks checks={checks} />
      </div>

      <div className="panel span-2">
        <div className="panel-head">
          <h3><span className="icon-badge"><Icon name="execute" size={14} /></span> Execution result</h3>
          <div className="meta">Simulation only · no real money moved</div>
        </div>
        <div className="exec-result">
          <div>
            <div className="small">Selected action</div>
            <div className="amount" style={{ fontSize: 18 }}>
              {strategyLabel(execution?.selected_strategy ?? selected)}
            </div>
          </div>
          <div>
            <div className="small">Execution status</div>
            <div className="amount" style={{ fontSize: 18 }}>
              <Chip variant={meta.variant}>{meta.label}</Chip>
            </div>
          </div>
          <div>
            <div className="small">Recovered (simulated)</div>
            <div className={`amount ${Number(execution?.recovered_amount ?? 0) > 0 ? "recovered" : ""}`}>
              {formatINRWithCents(execution?.recovered_amount ?? 0)}
            </div>
          </div>
          <div>
            <div className="small">Timestamp</div>
            <div className="amount" style={{ fontSize: 16, fontFamily: "var(--font-mono)" }}>
              {execution?.timestamp || "—"}
            </div>
          </div>
        </div>
        <div style={{ marginTop: 14, color: "var(--ink-500)", fontSize: 13, lineHeight: 1.6 }}>
          <strong style={{ color: "var(--ink-900)" }}>Reason: </strong>
          {execution?.reason || "No reason recorded."}
        </div>
        {guardrail?.checks?.length ? (
          <div style={{ marginTop: 14 }}>
            <h4 style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--ink-400)", margin: "0 0 8px", fontWeight: 500 }}>
              Guardrail checks
            </h4>
            <ul style={{ margin: 0, paddingLeft: 18, color: "var(--ink-500)", fontSize: 13, lineHeight: 1.6 }}>
              {guardrail.checks.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function CaseRow({ entry, expanded, onToggle }) {
  const { paymentId, decision } = entry;
  const diagnosis = decision?.diagnosis ?? {};
  const selected = decision?.selected_strategy;
  const ranked = decision?.optimization?.ranked_strategies ?? [];
  const winner = ranked.find((r) => r.strategy === selected) ?? ranked[0];
  const expected = winner?.expected_recovered_amount ?? decision?.expected_recovered_amount ?? 0;
  const outcome = decision?.execution?.simulated_outcome;
  const meta = statusMeta(outcome);
  const cat = categoryVariant(diagnosis.failure_category);

  return (
    <tr
      className={expanded ? "expanded" : ""}
      onClick={onToggle}
      style={{ cursor: "pointer" }}
    >
      <td className="id">{paymentId}</td>
      <td>
        <Chip variant={cat}>{failureLabel(diagnosis.failure_category)}</Chip>
      </td>
      <td>{strategyLabel(selected)}</td>
      <td className="amount">{formatINRWithCents(expected)}</td>
      <td>
        <Chip variant={meta.variant}>{meta.label}</Chip>
      </td>
      <td>
        <span className={`expand-indicator ${expanded ? "open" : ""}`} aria-hidden>
          <Icon name="back" size={12} />
        </span>
      </td>
    </tr>
  );
}

export default function AIDecisions() {
  const [state, setState] = useState({ status: "loading", cases: [], error: null });
  const [expandedId, setExpandedId] = useState(null);

  const load = async () => {
    setState({ status: "loading", cases: [], error: null });
    setExpandedId(null);
    try {
      // Strictly read-only: GET /payments/{id}/decision for each demo ID.
      // We never call POST /recover — opening or refreshing this page must
      // not mutate the backend.
      const cases = await Promise.all(
        REPRESENTATIVE_PAYMENT_IDS.map(async (id) => {
          try {
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
  const expandedEntry = expandedId
    ? ready.find((c) => c.paymentId === expandedId)
    : null;

  return (
    <>
      <section className="cases-table">
        <div className="cases-header">
          <div>
            <h3>
              <span className="icon-badge"><Icon name="ai" size={14} /></span>
              AI decision center
            </h3>
            <p>
              Read-only inspection of the persisted decision snapshots for the six
              representative demo payments. Click any row to see the full AI
              decision — diagnosis, eligible strategies, ranked outcomes, guardrails,
              and execution. No recovery is executed from this page.
            </p>
          </div>
          <div className="head-meta">
            Read-only snapshot · {ready.length} of {REPRESENTATIVE_PAYMENT_IDS.length} loaded
          </div>
        </div>
        {ready.length === 0 ? (
          <div className="state" style={{ padding: "36px 16px" }}>
            <strong>No demo decisions available</strong>
            <span>Ensure the backend's demo dataset is seeded.</span>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Payment</th>
                <th>Failure</th>
                <th>Selected strategy</th>
                <th>Expected recovery</th>
                <th>Outcome</th>
                <th style={{ width: 32 }}></th>
              </tr>
            </thead>
            <tbody>
              {ready.map((c) => (
                <CaseRow
                  key={c.paymentId}
                  entry={c}
                  expanded={expandedId === c.paymentId}
                  onToggle={() =>
                    setExpandedId((prev) => (prev === c.paymentId ? null : c.paymentId))
                  }
                />
              ))}
            </tbody>
          </table>
        )}
      </section>

      {expandedEntry ? (
        <DecisionDetail
          decision={expandedEntry.decision}
          paymentId={expandedEntry.paymentId}
          onCollapse={() => setExpandedId(null)}
        />
      ) : null}

      <div className="footer">
        <span>PayFix · AI Revenue Recovery Agent · Razorpay Buildathon · Track 03</span>
        <span><span className="heart">●</span> Simulation mode — no real payments processed</span>
      </div>
    </>
  );
}
