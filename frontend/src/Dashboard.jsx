import { useEffect, useState } from "react";
import { Icon } from "./Icon.jsx";
import { api, REPRESENTATIVE_PAYMENT_IDS } from "./api.js";
import {
  categoryVariant,
  failureLabel,
  formatINR,
  formatINRWithCents,
  formatInt,
  formatPct,
  formatPctSigned,
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

function Spinner({ label = "Loading…" }) {
  return (
    <div className="state">
      <div className="spinner" />
      <strong>{label}</strong>
      <span>Fetching the latest simulated recovery data from the backend.</span>
    </div>
  );
}

function ErrorState({ error, onRetry }) {
  return (
    <div className="state">
      <div className="icon"><Icon name="alert" size={18} /></div>
      <strong>Couldn't reach PayFix backend</strong>
      <span>{String(error?.message ?? error)}</span>
      {onRetry ? (
        <button className="back-link" onClick={onRetry}>Retry</button>
      ) : null}
    </div>
  );
}

function StrategyBar({ label, value, max, variant, displayValue }) {
  const pct = max > 0 ? Math.max(2, Math.min(100, (value / max) * 100)) : 2;
  return (
    <div className="funnel-row">
      <div className="funnel-label">{label}</div>
      <div className="funnel-bar-wrap">
        <div className={`funnel-bar ${variant}`} style={{ width: `${pct}%` }}>
          {variant === "payfix" || variant === "potential" ? displayValue : ""}
        </div>
      </div>
      <div className="funnel-value">{displayValue}</div>
    </div>
  );
}

function KpiCard({ icon, label, value, hint, valueClass = "" }) {
  return (
    <div className="kpi">
      <div className="label">
        <span className={`kpi-icon ${icon.color}`}><Icon name={icon.name} size={14} /></span>
        {label}
      </div>
      <div className={`value ${valueClass}`}>{value}</div>
      {hint ? <div className="hint">{hint}</div> : null}
    </div>
  );
}

function StrategyMetricRow({ strategy, metric }) {
  // The backend returns `strategy_metrics` as an OBJECT keyed by strategy
  // name, e.g. { retry_now: { count, successful_recoveries, recovered_amount } }.
  // The strategy name comes from the KEY, not from a field inside the metric.
  return (
    <tr>
      <td className="amount">{formatINRWithCents(metric?.recovered_amount)}</td>
      <td>{formatInt(metric?.count)}</td>
      <td>{strategyLabel(strategy)}</td>
    </tr>
  );
}

// `strategy_metrics` from /evaluation/run is an object keyed by strategy name.
// Be defensive: it can be null, an object, or (defensively) an array.
function strategyMetricsEntries(strategyMetrics) {
  if (strategyMetrics && typeof strategyMetrics === "object" && !Array.isArray(strategyMetrics)) {
    return Object.entries(strategyMetrics);
  }
  if (Array.isArray(strategyMetrics)) {
    // Allow either {strategy, ...} items or raw values, just in case.
    return strategyMetrics.map((item, idx) => [item?.strategy ?? String(idx), item]);
  }
  return [];
}

export default function Dashboard({ onSelectPayment }) {
  const [state, setState] = useState({ status: "loading", evaluation: null, cases: [], error: null });

  const load = async () => {
    setState({ status: "loading", evaluation: null, cases: [], error: null });
    try {
      const evaluation = await api.runEvaluation({ batchSize: 500 });
      const cases = await Promise.all(
        REPRESENTATIVE_PAYMENT_IDS.map(async (id) => {
          try {
            const decision = await api.recover(id);
            return { paymentId: id, decision, error: null };
          } catch (err) {
            return { paymentId: id, decision: null, error: err };
          }
        })
      );
      setState({ status: "ready", evaluation, cases, error: null });
    } catch (err) {
      setState({ status: "error", evaluation: null, cases: [], error: err });
    }
  };

  useEffect(() => { load(); }, []);

  if (state.status === "loading") return <Spinner label="Running batch evaluation…" />;
  if (state.status === "error") return <ErrorState error={state.error} onRetry={load} />;

  const ev = state.evaluation;
  const uplift = Number(ev.recovered_revenue_uplift);
  const upliftPct = Number(ev.recovered_revenue_uplift_percentage);
  const upliftTone = uplift > 0 ? "positive" : uplift < 0 ? "negative" : "neutral";
  const max = Math.max(
    Number(ev.revenue_at_risk || 0),
    Number(ev.payfix_recovered || 0),
    Number(ev.baseline_recovered || 0),
    1
  );
  const successfulRecoveries = (ev.successful_recoveries ?? 0);
  const humanEscalations = (ev.human_escalations ?? 0);
  const stoppedCases = (ev.stopped_cases ?? 0);
  const blockedActions = (ev.blocked_actions ?? 0);
  const cases = state.cases.filter((c) => c.decision);
  const strategyMetrics = ev.strategy_metrics ?? {};
  const strategyEntries = strategyMetricsEntries(strategyMetrics);

  return (
    <>
      {/* Hero with the four focal KPIs */}
      <section className="hero">
        <div className="hero-eyebrow">
          <span className="ai-chip"><Icon name="ai" size={12} /> AI Revenue Recovery Agent</span>
          <span>Batch performance · {formatInt(ev.payments_evaluated)} failed payments evaluated</span>
        </div>
        <h2>PayFix recovers revenue your gateway already wrote off.</h2>
        <p className="tagline">
          Diagnose every failed payment, simulate every recovery strategy, and execute only the safest, highest-value path — automatically.
        </p>
        <div className="hero-stats">
          <div className="hero-stat">
            <span className="label">Revenue at risk</span>
            <div className="value neutral">{formatINR(ev.revenue_at_risk)}</div>
            <div className="delta">{formatInt(ev.payments_evaluated)} failed payments · across the synthetic batch</div>
          </div>
          <div className="hero-stat focal">
            <span className="label">PayFix recovered</span>
            <div className="value positive">{formatINR(ev.payfix_recovered)}</div>
            <div className="delta">{formatPct(ev.payfix_recovery_rate)} of at-risk revenue</div>
          </div>
          <div className="hero-stat">
            <span className="label">Revenue uplift</span>
            <div className={`value ${upliftTone === "positive" ? "positive" : "warn"}`}>
              {uplift >= 0 ? "+" : "−"}{formatINR(Math.abs(uplift))}
            </div>
            <div className="delta"><strong>{formatPctSigned(upliftPct)}</strong> over naive retry baseline</div>
          </div>
          <div className="hero-stat">
            <span className="label">Recovery rate</span>
            <div className="value neutral">{formatPct(ev.payfix_recovery_rate)}</div>
            <div className="delta">Baseline {formatPct(ev.baseline_recovery_rate)} · PayFix {formatPct(ev.payfix_recovery_rate)}</div>
          </div>
        </div>
      </section>

      {/* Recovery story visualization */}
      <section className="story">
        <div className="card">
          <div className="card-head">
            <div>
              <h3>Revenue recovery funnel</h3>
              <p>How much revenue PayFix actually recovered from the at-risk pool.</p>
            </div>
            <div className="head-meta">Synthetic batch · {formatInt(ev.payments_evaluated)} payments</div>
          </div>
          <div className="funnel">
            <StrategyBar
              label="At risk"
              value={Number(ev.revenue_at_risk)}
              max={max}
              variant="at-risk"
              displayValue={formatINR(ev.revenue_at_risk)}
            />
            <div className="funnel-arrow"><Icon name="arrow" size={14} /></div>
            <StrategyBar
              label="Baseline"
              value={Number(ev.baseline_recovered)}
              max={max}
              variant="baseline"
              displayValue={formatINR(ev.baseline_recovered)}
            />
            <div className="funnel-arrow"><Icon name="arrow" size={14} /></div>
            <StrategyBar
              label="PayFix"
              value={Number(ev.payfix_recovered)}
              max={max}
              variant="payfix"
              displayValue={formatINR(ev.payfix_recovered)}
            />
          </div>
        </div>

        <div className="card uplift-card">
          <div className="card-head">
            <div>
              <h3><span className="icon-badge"><Icon name="ai" size={14} /></span> Uplift over baseline</h3>
              <p>Additional revenue PayFix recovered vs. naive retry.</p>
            </div>
          </div>
          <div>
            <div className={`uplift-pct ${upliftTone}`}>
              <Icon name="trend" size={12} />
              {formatPctSigned(upliftPct)} vs baseline
            </div>
            <div className={`uplift-figure ${upliftTone}`}>
              <span className="currency">₹</span>
              {formatINR(Math.abs(uplift))}
            </div>
            <div className="uplift-meta">
              PayFix recovered <strong>{formatINR(ev.payfix_recovered)}</strong> against a naive retry baseline of <strong>{formatINR(ev.baseline_recovered)}</strong>.
              Every simulated action is bounded by guardrails before execution.
            </div>
          </div>
        </div>
      </section>

      {/* Smaller KPI tiles for the operationally important numbers */}
      <section className="kpi-grid">
        <KpiCard
          icon={{ name: "check", color: "success" }}
          label="Successful recoveries"
          value={formatInt(successfulRecoveries)}
          hint="Simulated recovery outcome"
        />
        <KpiCard
          icon={{ name: "human", color: "escalation" }}
          label="Human escalations"
          value={formatInt(humanEscalations)}
          hint="Routed to human review"
        />
        <KpiCard
          icon={{ name: "stop", color: "stopped" }}
          label="Stopped cases"
          value={formatInt(stoppedCases)}
          hint="No recovery warranted"
        />
        <KpiCard
          icon={{ name: "block", color: "blocked" }}
          label="Blocked actions"
          value={formatInt(blockedActions)}
          hint="Refused by guardrails"
        />
      </section>

      {/* Strategy mix table */}
      <section className="card" style={{ marginBottom: 28 }}>
        <div className="card-head">
          <div>
            <h3>Strategy mix across the batch</h3>
            <p>How PayFix distributed simulated recovery actions across strategies.</p>
          </div>
        </div>
        {strategyEntries.length === 0 ? (
          <div className="state" style={{ padding: "32px 16px" }}>
            <strong>No strategy metrics returned</strong>
            <span>The batch did not contain any eligible recovery strategies.</span>
          </div>
        ) : (
          <div className="cases-table" style={{ marginTop: 4 }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: "30%" }}>Strategy</th>
                  <th style={{ width: "20%" }}>Payments</th>
                  <th style={{ width: "25%" }}>Recovered (simulated)</th>
                </tr>
              </thead>
              <tbody>
                {strategyEntries.map(([strategy, metric]) => (
                  <StrategyMetricRow key={strategy} strategy={strategy} metric={metric} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Representative recovery cases */}
      <section className="cases-table">
        <div className="cases-header">
          <div>
            <h3><span className="icon-badge"><Icon name="queue" size={14} /></span> Representative recovery cases</h3>
            <p>
              Six illustrative payments from the backend's demo dataset.
              Click any row to see the full AI decision for that payment — diagnosis, simulation, guardrails, and execution.
            </p>
          </div>
          <div className="head-meta">Backend demo dataset · 6 of {formatInt(ev.payments_evaluated)}</div>
        </div>
        {cases.length === 0 ? (
          <div className="state" style={{ padding: "36px 16px" }}>
            <strong>No demo payments available</strong>
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
                <th>Recovered</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => {
                const decision = c.decision;
                const diagnosis = decision?.diagnosis ?? {};
                const selected = decision?.selected_strategy;
                const ranked = decision?.optimization?.ranked_strategies ?? [];
                const winner = ranked.find((r) => r.strategy === selected) ?? ranked[0];
                const expected = winner?.expected_recovered_amount ?? decision?.expected_recovered_amount;
                const recovered = Number(decision?.execution?.recovered_amount ?? 0);
                const meta = statusMeta(decision?.execution?.simulated_outcome);
                return (
                  <tr key={c.paymentId} onClick={() => onSelectPayment(c.paymentId, decision)}>
                    <td className="id">{c.paymentId}</td>
                    <td>
                      <Chip variant={categoryVariant(diagnosis.failure_category)}>
                        {failureLabel(diagnosis.failure_category)}
                      </Chip>
                    </td>
                    <td>{strategyLabel(selected)}</td>
                    <td className="amount">{formatINRWithCents(expected)}</td>
                    <td className="amount">{formatINRWithCents(recovered)}</td>
                    <td>
                      <Chip variant={meta.variant}>{meta.label}</Chip>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <div className="footer">
        <span>PayFix · AI Revenue Recovery Agent · Razorpay Buildathon · Track 03</span>
        <span><span className="heart">●</span> Simulation mode — no real payments processed</span>
      </div>
    </>
  );
}
