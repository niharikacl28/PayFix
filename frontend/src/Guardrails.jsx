import { useEffect, useMemo, useState } from "react";
import { Icon } from "./Icon.jsx";
import { api, REPRESENTATIVE_PAYMENT_IDS } from "./api.js";
import {
  categoryVariant,
  failureLabel,
  formatINRWithCents,
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

function Spinner({ label = "Loading guardrail decisions…" }) {
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
      <strong>Couldn't load guardrail decisions</strong>
      <span>{String(error?.message ?? error)}</span>
      {onRetry ? (
        <button className="back-link" onClick={onRetry}>Retry</button>
      ) : null}
    </div>
  );
}

// The "pulse" of the page: the real, merchant-tunable guardrail limits that
// the deterministic backend (`backend/app/guardrails.py` -> `GuardrailConfig`
// + `RETRY_BLOCKED_CATEGORIES`) actually enforces. These are read from the
// source-of-truth here so the page never invents values.
const GUARDRAIL_LIMITS = {
  maxAutomatedRetries: 2,
  maxAutomatedCustomerContacts: 2,
  maxAutomatedPaymentAmount: 10000, // INR
  blockHighRiskAutomation: true,
  retryBlockedCategories: ["permanent_decline", "expired_card", "expired_payment_method"],
  automatedStrategies: [
    "retry_now",
    "retry_later",
    "payment_link",
    "alternate_payment_method",
    "customer_reminder",
  ],
  humanEscalation: "Human escalation is always permitted as the safe manual-review fallback.",
  stopStrategy: "Stop is always allowed as the safe no-op fallback.",
};

function formatINR(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(Number(value));
}

const PIPELINE = [
  { id: "ai", name: "AI recommendation", icon: "ai" },
  { id: "eligibility", name: "Eligibility", icon: "diagnose" },
  { id: "policy", name: "Policy checks", icon: "guardrails" },
  { id: "execution", name: "Execution decision", icon: "execute" },
  { id: "audit", name: "Audit", icon: "audit" },
];

// Derive which pipeline stages the guardrail result for a payment reached.
// `fail` halts the pipeline (fails fast on the policy / execution stage).
function derivePipelineState({ decision }) {
  const guardrail = decision?.guardrail_result;
  const execution = decision?.execution;
  const states = ["ai", "eligibility", "policy"]; // AI -> eligibility -> policy always run
  if (guardrail) {
    if (guardrail.allowed) {
      states.push("execution");
    } else {
      // Policy stage fails fast: the rest never runs.
      return states.map((id) => (id === "policy" ? "fail" : "done"));
    }
  } else {
    return states.map(() => "fail");
  }
  if (execution && execution.simulated_outcome !== "blocked") {
    states.push("audit");
  } else if (execution) {
    return states.map((id) => (id === "audit" ? "fail" : "done"));
  }
  return states.map((id) => "done");
}

function GuardrailPipeline({ stageStates }) {
  const states = PIPELINE.map((s) => {
    const candidate = stageStates.find((x) => x.id === s.id);
    return candidate ? candidate.state : "todo";
  });
  const firstTodo = states.indexOf("todo");
  if (firstTodo !== -1) states[firstTodo] = "active";
  const done = states.filter((s) => s === "done").length;
  const progressPct = (done / PIPELINE.length) * 100;

  return (
    <section className="flow">
      <div className="flow-head">
        <div>
          <h3>Guardrail pipeline</h3>
          <p>AI recommendation → Eligibility → Policy checks → Execution decision → Audit.</p>
        </div>
        <div className="head-meta">{progressPct.toFixed(0)}% reached</div>
      </div>
      <div
        className={`flow-stages ${states.includes("active") ? "active" : ""}`}
        style={{ "--flow-progress": `${progressPct}%` }}
      >
        {PIPELINE.map((stage, idx) => (
          <div key={stage.id} className={`flow-stage ${states[idx]}`}>
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

function PolicyCheckRow({ index, text }) {
  // `guardrail_result.checks` is a list of short strings. We render each
  // as a single check line, similar in shape to the per-check items in
  // PaymentDetail's `GuardrailChecks`.
  return (
    <div className="check pass">
      <span className="mark"><Icon name="check" size={11} strokeWidth={2.4} /></span>
      <div>
        <div><strong>Check {index + 1}</strong></div>
        <div style={{ color: "var(--ink-500)", marginTop: 2, fontSize: 12.5 }}>{text}</div>
      </div>
    </div>
  );
}

// The status of a single payment's guardrail flow, expressed as one of three
// visible states: ALLOWED, HUMAN REVIEW, BLOCKED. This is the only piece
// of "new" presentation logic; everything else comes straight from the
// `guardrail_result` and `execution` payload.
function guardrailStatusOf(decision) {
  const guardrail = decision?.guardrail_result;
  const execution = decision?.execution;
  if (!guardrail) {
    return { key: "blocked", label: "BLOCKED", variant: "blocked" };
  }
  if (!guardrail.allowed) {
    return { key: "blocked", label: "BLOCKED", variant: "blocked" };
  }
  if (guardrail.strategy === "human_escalation" || execution?.simulated_outcome === "simulated_human_review_queued") {
    return { key: "human", label: "HUMAN REVIEW", variant: "escalation" };
  }
  if (execution && execution.simulated_outcome === "blocked") {
    return { key: "blocked", label: "BLOCKED", variant: "blocked" };
  }
  return { key: "allowed", label: "ALLOWED", variant: "success" };
}

function RepresentativeRow({ entry, expanded, onToggle }) {
  const { paymentId, decision } = entry;
  const diagnosis = decision?.diagnosis ?? {};
  const selected = decision?.selected_strategy;
  const recovered = decision?.execution?.recovered_amount;
  const outcome = decision?.execution?.simulated_outcome;
  const status = guardrailStatusOf(decision);
  const executionAllowed = !!decision?.guardrail_result?.allowed && outcome !== "blocked";
  const outMeta = statusMeta(outcome);

  return (
    <tr
      className={expanded ? "expanded" : ""}
      onClick={onToggle}
      style={{ cursor: "pointer" }}
    >
      <td className="id">{paymentId}</td>
      <td>
        <Chip variant={categoryVariant(diagnosis.failure_category)}>
          {failureLabel(diagnosis.failure_category)}
        </Chip>
      </td>
      <td>{strategyLabel(selected)}</td>
      <td>
        <Chip variant={status.variant}>{status.label}</Chip>
      </td>
      <td>
        <Chip variant={executionAllowed ? "success" : "blocked"}>
          {executionAllowed ? "Yes" : "No"}
        </Chip>
      </td>
      <td>
        <Chip variant={outMeta.variant}>{outMeta.label}</Chip>
      </td>
      <td className="amount">{formatINRWithCents(recovered)}</td>
    </tr>
  );
}

function DecisionGuardrailDetail({ decision, paymentId, onCollapse }) {
  const diagnosis = decision?.diagnosis ?? {};
  const selected = decision?.selected_strategy;
  const guardrail = decision?.guardrail_result ?? {};
  const execution = decision?.execution ?? {};
  const checks = Array.isArray(guardrail.checks) ? guardrail.checks : [];
  const stageStates = useMemo(() => {
    const states = [];
    const all = ["ai", "eligibility", "policy", "execution", "audit"];
    for (const id of all) {
      states.push({ id, state: "todo" });
    }
    const derived = derivePipelineState({ decision });
    for (let i = 0; i < all.length; i += 1) {
      states[i].state = derived[i] ?? "todo";
    }
    return states;
  }, [decision]);
  const status = guardrailStatusOf(decision);
  const executionAllowed = !!guardrail.allowed && execution.simulated_outcome !== "blocked";

  return (
    <section className="detail-grid" style={{ marginTop: 18 }}>
      <div className="panel span-2">
        <div className="panel-head">
          <h3>
            <span className="icon-badge"><Icon name="shield" size={14} /></span>
            Guardrail decision · {paymentId}
          </h3>
          <button className="back-link" onClick={onCollapse}>
            <Icon name="back" size={14} /> Collapse
          </button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18, alignItems: "start" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
              <Chip variant={status.variant}>{status.label}</Chip>
              <span style={{ color: "var(--ink-500)", fontSize: 13 }}>
                {status.key === "allowed" && "Policy checks passed; execution was permitted."}
                {status.key === "human" && "Routed to a human reviewer instead of automated action."}
                {status.key === "blocked" && "Blocked by deterministic backend rules."}
              </span>
            </div>
            <div className="reasoning">
              <div className="ai-mark"><Icon name="shield" size={14} /></div>
              <div className="body">
                <strong>Guardrail reason: </strong>
                {guardrail.reason || "No guardrail reason was returned for this decision."}
              </div>
            </div>
          </div>
          <div>
            <div style={{ marginTop: 4 }}>
              <dl className="kv">
                <span style={{ display: "contents" }}>
                  <dt>Selected strategy</dt>
                  <dd>{strategyLabel(selected)}</dd>
                </span>
                <span style={{ display: "contents" }}>
                  <dt>Failure category</dt>
                  <dd>{failureLabel(diagnosis.failure_category)}</dd>
                </span>
                <span style={{ display: "contents" }}>
                  <dt>Guardrail allowed</dt>
                  <dd>
                    <Chip variant={guardrail.allowed ? "success" : "blocked"}>
                      {guardrail.allowed ? "Yes" : "No"}
                    </Chip>
                  </dd>
                </span>
                <span style={{ display: "contents" }}>
                  <dt>Execution allowed</dt>
                  <dd>
                    <Chip variant={executionAllowed ? "success" : "blocked"}>
                      {executionAllowed ? "Yes" : "No"}
                    </Chip>
                  </dd>
                </span>
                <span style={{ display: "contents" }}>
                  <dt>Execution outcome</dt>
                  <dd>
                    <Chip variant={statusMeta(execution.simulated_outcome).variant}>
                      {statusMeta(execution.simulated_outcome).label}
                    </Chip>
                  </dd>
                </span>
                <span style={{ display: "contents" }}>
                  <dt>Recovered (simulated)</dt>
                  <dd>{formatINRWithCents(execution.recovered_amount ?? 0)}</dd>
                </span>
              </dl>
            </div>
          </div>
        </div>
      </div>

      <GuardrailPipeline stageStates={stageStates} />

      <div className="panel span-2">
        <div className="panel-head">
          <h3>
            <span className="icon-badge"><Icon name="check" size={14} /></span>
            Guardrail checks
          </h3>
          <div className="meta">{checks.length} check{checks.length === 1 ? "" : "s"}</div>
        </div>
        {checks.length === 0 ? (
          <div className="state" style={{ padding: "20px 12px" }}>
            <strong>No guardrail checks returned</strong>
            <span>This decision's guardrail did not record any individual checks.</span>
          </div>
        ) : (
          <div className="checks">
            {checks.map((text, i) => (
              <PolicyCheckRow key={i} index={i} text={text} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export default function Guardrails() {
  const [state, setState] = useState({ status: "loading", cases: [], error: null });
  const [expandedId, setExpandedId] = useState(null);

  const load = async () => {
    setState({ status: "loading", cases: [], error: null });
    setExpandedId(null);
    try {
      // Strictly read-only: GET /payments/{id}/decision for each demo ID.
      // Never POST /recover; opening or refreshing this page must not mutate
      // the backend.
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
      setState({ status: "error", cases: [], error: null, error: err });
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
      {/* Strong simulation-only notice */}
      <div className="sim-notice" style={{ marginBottom: 16 }}>
        <span className="sim-dot" />
        <span>
          <strong>Simulation mode.</strong> No real payments are processed.
          Guardrails are demonstrated against synthetic payment data.
        </span>
      </div>

      {/* Explainer: AI recommends, guardrails decide */}
      <section className="card" style={{ marginBottom: 18 }}>
        <div className="card-head">
          <div>
            <h3>
              <span className="icon-badge"><Icon name="shield" size={14} /></span>
              AI recommends. PayFix guardrails decide.
            </h3>
            <p>
              The AI optimizer can recommend a strategy, but deterministic
              backend rules decide whether execution is allowed. Every simulated
              action is bounded by these guardrails before any money or
              customer communication is moved.
            </p>
          </div>
        </div>
      </section>

      {/* Core merchant safety limits, sourced from GuardrailConfig. */}
      <section className="card" style={{ marginBottom: 18 }}>
        <div className="card-head">
          <div>
            <h3><span className="icon-badge"><Icon name="guardrails" size={14} /></span> Merchant safety limits</h3>
            <p>The deterministic limits the backend applies to every recovery action.</p>
          </div>
          <div className="head-meta">From backend configuration</div>
        </div>
        <div className="kpi-grid" style={{ marginTop: 4 }}>
          <div className="kpi">
            <div className="label">
              <span className="kpi-icon warn"><Icon name="execute" size={14} /></span>
              Maximum automated retries
            </div>
            <div className="value neutral">{GUARDRAIL_LIMITS.maxAutomatedRetries}</div>
            <div className="hint">Per payment, per session</div>
          </div>
          <div className="kpi">
            <div className="label">
              <span className="kpi-icon escalation"><Icon name="human" size={14} /></span>
              Maximum customer contacts
            </div>
            <div className="value neutral">{GUARDRAIL_LIMITS.maxAutomatedCustomerContacts}</div>
            <div className="hint">Payment link + reminders per payment</div>
          </div>
          <div className="kpi">
            <div className="label">
              <span className="kpi-icon success"><Icon name="money" size={14} /></span>
              Maximum automated amount
            </div>
            <div className="value neutral">{formatINR(GUARDRAIL_LIMITS.maxAutomatedPaymentAmount)}</div>
            <div className="hint">Above this, route to human escalation</div>
          </div>
          <div className="kpi">
            <div className="label">
              <span className="kpi-icon danger"><Icon name="block" size={14} /></span>
              Fraud &amp; high-risk protection
            </div>
            <div className="value neutral">
              {GUARDRAIL_LIMITS.blockHighRiskAutomation ? "Enabled" : "Disabled"}
            </div>
            <div className="hint">Automatic actions are blocked for high-risk or suspected-fraud payments</div>
          </div>
        </div>
        <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
          <div>
            <h4 style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--ink-400)", margin: "0 0 8px", fontWeight: 500 }}>
              Permanent-decline protection
            </h4>
            <div style={{ color: "var(--ink-700)", fontSize: 13.5, lineHeight: 1.6 }}>
              Automatic retry is blocked for these failure categories:
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                {GUARDRAIL_LIMITS.retryBlockedCategories.map((c) => (
                  <Chip key={c} variant="blocked">{c.replace(/_/g, " ")}</Chip>
                ))}
              </div>
            </div>
          </div>
          <div>
            <h4 style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--ink-400)", margin: "0 0 8px", fontWeight: 500 }}>
              High-value / unusual-case protection
            </h4>
            <div style={{ color: "var(--ink-700)", fontSize: 13.5, lineHeight: 1.6 }}>
              <p style={{ margin: "0 0 6px" }}>
                Payments above {formatINR(GUARDRAIL_LIMITS.maxAutomatedPaymentAmount)} cannot be
                recovered automatically; they are routed to human escalation.
              </p>
              <p style={{ margin: 0 }}>
                {GUARDRAIL_LIMITS.humanEscalation}
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Representative guardrail decisions table */}
      <section className="cases-table">
        <div className="cases-header">
          <div>
            <h3>
              <span className="icon-badge"><Icon name="queue" size={14} /></span>
              Representative guardrail decisions
            </h3>
            <p>
              Read-only inspection of how the six representative demo payments
              flowed through PayFix's guardrail pipeline. Click any row to see
              the full guardrail decision, checks, and execution outcome.
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
                <th>Guardrail</th>
                <th>Execution allowed</th>
                <th>Outcome</th>
                <th>Recovered</th>
              </tr>
            </thead>
            <tbody>
              {ready.map((c) => (
                <RepresentativeRow
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
        <DecisionGuardrailDetail
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
