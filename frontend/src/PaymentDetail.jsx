import { useEffect, useMemo, useState } from "react";
import { Icon } from "./Icon.jsx";
import { api } from "./api.js";
import {
  failureLabel,
  formatINRWithCents,
  formatPct,
  statusMeta,
  strategyLabel,
} from "./format.js";

const FLOW = [
  { id: "detect", name: "Detect", icon: "detect" },
  { id: "diagnose", name: "Diagnose", icon: "diagnose" },
  { id: "simulate", name: "Simulate", icon: "simulate" },
  { id: "optimize", name: "Optimize", icon: "optimize" },
  { id: "guardrails", name: "Guardrails", icon: "guardrails" },
  { id: "execute", name: "Execute", icon: "execute" },
  { id: "audit", name: "Audit", icon: "audit" },
];

function Chip({ variant = "neutral", children }) {
  return (
    <span className={`chip ${variant}`}>
      <span className="dot" />
      {children}
    </span>
  );
}

function Spinner({ label = "Loading decision…" }) {
  return (
    <div className="state">
      <div className="spinner" />
      <strong>{label}</strong>
      <span>Running diagnose · optimize · guardrails · execute for this payment.</span>
    </div>
  );
}

function ErrorState({ error, onBack, onRetry }) {
  return (
    <div className="state">
      <div className="icon"><Icon name="alert" size={18} /></div>
      <strong>Couldn't load this decision</strong>
      <span>{String(error?.message ?? error)}</span>
      <div style={{ display: "flex", gap: 16 }}>
        {onBack ? <button className="back-link" onClick={onBack}><Icon name="back" size={14} /> Back to dashboard</button> : null}
        {onRetry ? <button className="back-link" onClick={onRetry}>Retry</button> : null}
      </div>
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

function RecoveryFlow({ completedStages, blockedAtStage }) {
  const stageStates = FLOW.map((s) => {
    if (blockedAtStage && FLOW.findIndex((x) => x.id === blockedAtStage) <= FLOW.findIndex((x) => x.id === s.id)) {
      return "fail";
    }
    if (completedStages.includes(s.id)) return "done";
    return "todo";
  });
  const firstTodo = stageStates.indexOf("todo");
  if (firstTodo !== -1) stageStates[firstTodo] = "active";
  const progressPct = ((stageStates.filter((s) => s === "done").length) / FLOW.length) * 100;

  return (
    <section className="flow">
      <div className="flow-head">
        <div>
          <h3>Recovery flow</h3>
          <p>Detect → Diagnose → Simulate → Optimize → Guardrails → Execute → Audit.</p>
        </div>
        <div className="head-meta">{progressPct.toFixed(0)}% complete</div>
      </div>
      <div
        className={`flow-stages ${stageStates.includes("active") ? "active" : ""}`}
        style={{ "--flow-progress": `${progressPct}%` }}
      >
        {FLOW.map((stage, idx) => (
          <div key={stage.id} className={`flow-stage ${stageStates[idx]}`}>
            <div className="flow-node">
              <Icon name={stage.icon} size={18} />
            </div>
            <div className="name">{stage.name}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

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

function deriveBlockedStage({ decision }) {
  const execution = decision?.execution;
  const guardrail = decision?.guardrail_result;
  if (guardrail && !guardrail.allowed) return "guardrails";
  if (execution?.simulated_outcome === "blocked") return "execute";
  return null;
}

function deriveCompletedStages({ decision }) {
  const execution = decision?.execution;
  const guardrail = decision?.guardrail_result;
  const completed = ["detect", "diagnose", "simulate", "optimize"];
  if (guardrail) completed.push("guardrails");
  if (execution && execution.simulated_outcome !== "blocked") completed.push("execute");
  if (execution?.timestamp) completed.push("audit");
  return completed;
}

// Builds the human-readable "what this payment looked like" panel from
// fields the API actually returns (DiagnosisResult + OptimizationResult).
function buildContext({ decision, paymentId }) {
  const diagnosis = decision?.diagnosis ?? {};
  const ranked = decision?.optimization?.ranked_strategies ?? [];
  const winner = ranked.find((r) => r.strategy === decision?.selected_strategy) ?? ranked[0];
  return {
    paymentId,
    failureCategory: diagnosis.failure_category,
    likelyCause: diagnosis.likely_cause,
    retryability: diagnosis.retryability_assessment,
    riskObservation: diagnosis.risk_observation,
    timingObservation: diagnosis.timing_observation,
    customerContext: diagnosis.customer_context_observation,
    confidence: diagnosis.confidence,
    eligibleStrategyNames: diagnosis.eligible_strategy_names ?? [],
    expectedRecovery: decision?.expected_recovered_amount,
    successProbability: winner?.success_probability,
  };
}

export default function PaymentDetail({ paymentId, initialDecision, onBack }) {
  const [state, setState] = useState(() => ({
    status: initialDecision ? "ready" : "loading",
    decision: initialDecision ?? null,
    error: null,
  }));

  const load = async () => {
    setState({ status: "loading", decision: null, error: null });
    try {
      const decision = await api.recover(paymentId);
      setState({ status: "ready", decision, error: null });
    } catch (err) {
      setState({ status: "error", decision: null, error: err });
    }
  };

  useEffect(() => {
    if (!initialDecision) load();
  }, [paymentId]);

  if (state.status === "loading") return <Spinner />;
  if (state.status === "error") return <ErrorState error={state.error} onBack={onBack} onRetry={load} />;

  const decision = state.decision;
  const context = buildContext({ decision, paymentId });
  const diagnosis = decision?.diagnosis ?? {};
  const ranked = decision?.optimization?.ranked_strategies ?? [];
  const selected = decision?.selected_strategy ?? ranked[0]?.strategy;
  const execution = decision?.execution ?? {};
  const guardrail = decision?.guardrail_result ?? {};
  const meta = statusMeta(execution?.simulated_outcome);
  const categoryVariant = (() => {
    switch (context.failureCategory) {
      case "insufficient_funds": return "warn";
      case "expired_payment_method":
      case "permanent_decline": return "blocked";
      case "suspected_fraud": return "danger";
      default: return "neutral";
    }
  })();

  const checks = useMemo(() => buildChecks({ decision }), [decision]);
  const blockedAtStage = useMemo(() => deriveBlockedStage({ decision }), [decision]);
  const completedStages = useMemo(() => deriveCompletedStages({ decision }), [decision]);

  return (
    <>
      <button className="back-link" onClick={onBack}>
        <Icon name="back" size={14} /> Back to dashboard
      </button>

      <div className="page-header" style={{ marginTop: 16 }}>
        <div>
          <span className="eyebrow">AI Decision Detail</span>
          <h1>{paymentId}</h1>
          <div className="subtitle">
            <Chip variant={categoryVariant}>{failureLabel(context.failureCategory)}</Chip>
            {" "}
            <Chip variant={meta.variant}>{meta.label}</Chip>
          </div>
        </div>
        <div className="sim-notice">
          <span className="sim-dot" />
          Simulation mode — no real payments processed
        </div>
      </div>

      {/* Payment summary + recovery amounts (top row) */}
      <section className="detail-grid">
        <div className="panel span-2">
          <div className="panel-head">
            <h3><span className="icon-badge"><Icon name="money" size={14} /></span> Payment summary</h3>
            <div className="meta">Payment ID · {paymentId}</div>
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
                  <div className="amount">{formatINRWithCents(context.expectedRecovery ?? 0)}</div>
                </div>
              </div>
              <div style={{ marginTop: 14 }}>
                <KV items={[
                  ["Failure category", failureLabel(context.failureCategory)],
                  ["Likely cause", context.likelyCause ?? "—"],
                  ["Retryability", context.retryability ?? "—"],
                  ["Diagnosis confidence", formatPct(context.confidence, 0)],
                  ["Selected strategy", strategyLabel(selected)],
                ]} />
              </div>
            </div>
            <div>
              <div style={{ marginTop: 14 }}>
                <KV items={[
                  ["Risk observation", context.riskObservation ?? "—"],
                  ["Timing observation", context.timingObservation ?? "—"],
                  ["Customer context", context.customerContext ?? "—"],
                  ["Success probability", formatPct(context.successProbability, 0)],
                  ["Execution timestamp", execution?.timestamp ?? "—"],
                ]} />
              </div>
            </div>
          </div>
        </div>

        {/* Diagnosis */}
        <div className="panel">
          <div className="panel-head">
            <h3><span className="icon-badge"><Icon name="diagnose" size={14} /></span> AI diagnosis</h3>
            <div className="meta">Failure analysis</div>
          </div>
          <div className="explain">
            {diagnosis.explanation ?? "The diagnosis service did not return an explanation for this payment."}
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
              <div style={{ fontSize: 14, fontWeight: 600 }}>{context.likelyCause ?? failureLabel(context.failureCategory)}</div>
            </div>
            <div className="panel-section">
              <h4>Eligible strategies</h4>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {(context.eligibleStrategyNames ?? []).length === 0 ? (
                  <Chip variant="blocked">None</Chip>
                ) : (
                  context.eligibleStrategyNames.map((s) => (
                    <Chip key={s} variant="neutral">{strategyLabel(s)}</Chip>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Strategy simulation */}
        <div className="panel">
          <div className="panel-head">
            <h3><span className="icon-badge"><Icon name="simulate" size={14} /></span> Recovery strategy simulator</h3>
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
      </section>

      {/* Recovery flow timeline */}
      <RecoveryFlow completedStages={completedStages} blockedAtStage={blockedAtStage} />

      {/* AI reasoning + guardrails + execution */}
      <section className="detail-grid">
        <div className="panel">
          <div className="panel-head">
            <h3><span className="icon-badge"><Icon name="ai" size={14} /></span> Why PayFix chose this</h3>
            <div className="meta">AI reasoning</div>
          </div>
          <div className="reasoning">
            <div className="ai-mark"><Icon name="ai" size={14} /></div>
            <div className="body">
              {decision?.selection_reason ?? "No reasoning was recorded for this decision."}
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
                {execution?.timestamp ?? "—"}
              </div>
            </div>
          </div>
          <div style={{ marginTop: 14, color: "var(--ink-500)", fontSize: 13, lineHeight: 1.6 }}>
            <strong style={{ color: "var(--ink-900)" }}>Reason: </strong>
            {execution?.reason ?? "No reason recorded."}
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

      <div className="footer">
        <span>PayFix · AI Revenue Recovery Agent · Razorpay Buildathon · Track 03</span>
        <span><span className="heart">●</span> Simulation mode — no real payments processed</span>
      </div>
    </>
  );
}
