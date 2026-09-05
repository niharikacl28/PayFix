# PayFix — AI Revenue Recovery Agent

> **Razorpay Buildathon · Track 03 · AI Revenue Recovery**
> *Find revenue that's slipping away and win it back.*

PayFix is a buildathon-grade, simulation-only revenue-recovery agent for failed
payments. For every failed payment, PayFix **diagnoses** the root cause,
**simulates** every eligible recovery strategy, **optimizes** for the highest
expected recovery, and runs the resulting decision through a **deterministic
guardrail pipeline** before recording the simulated outcome in an immutable
audit trail. No real money ever moves; no real customer communications are
sent.

---

## Why PayFix is different

> **PayFix doesn't just retry failed payments. It determines the best
> recovery action for each payment while staying within merchant-defined
> safety limits.**

- **Diagnose, don't guess.** A structured AI diagnosis engine classifies the
  failure (temporary network, insufficient funds, expired card, permanent
  decline, suspected fraud, recurring mandate, …) with an explicit confidence
  score, a likely cause, and a tail of human-readable risk, timing, and
  customer-context observations.
- **Eligibility is authoritative.** A deterministic eligibility engine decides
  which recovery strategies are even *allowed* for a given payment. The AI
  never invents unavailable methods.
- **Simulate before you commit.** Every eligible strategy is run through a
  transparent simulator. Expected recovery is `payment amount × simulated
  success probability`, with documented adjustments for failure category,
  customer history, retry count, contact fatigue, and timing. Estimates are
  comparison aids, not real-world predictions.
- **Optimize, then guard.** The expected-recovery optimizer picks the top
  strategy, but a separate deterministic guardrail module is the final
  authority on whether execution is allowed.
- **Read-only inspection after the fact.** Once a decision is made, the
  detail page returns the *original* decision snapshot verbatim — opening
  or reloading a payment never re-executes recovery and never mutates the
  database.

---

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │                         React + Vite Frontend                        │
 │  Dashboard · Recovery Queue · AI Decisions · Guardrails · Detail     │
 └────────────────────────────┬─────────────────────────────────────────┘
                              │  read-only REST (no real-money path)
 ┌────────────────────────────▼─────────────────────────────────────────┐
 │                       FastAPI (simulation-only)                      │
 │  /health · /payments/{id}/diagnose · /optimize · /recover · /decision│
 │  /evaluation/run                                                    │
 │ ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────────┐ │
 │ │ Diagnosis  │  │ Eligibility│  │ Strategy   │  │ Recovery Service │ │
 │ │ Engine     │  │ Engine     │  │ Simulator  │  │ + Optimizer      │ │
 │ │  (AI / mock)│ │ (determin.)│  │ (determin.)│  │ (decision logic) │ │
 │ └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └────────┬─────────┘ │
 │       └──────┬─────────┴──────────┬────┘                │           │
 │ ┌────────────▼────────────────────▼────────────────────▼──────────┐   │
 │ │             Deterministic Guardrails (policy layer)             │   │
 │ └────────────┬───────────────────────────────────────────────────┘   │
 │ ┌────────────▼───────────────────────────────────────────────────┐   │
 │ │          Simulated Recovery Executor (no real money)           │   │
 │ └────────────┬───────────────────────────────────────────────────┘   │
 │ ┌────────────▼───────────────────────────────────────────────────┐   │
 │ │      Audit Log · Recovery Attempts · Decision Snapshots         │   │
 │ └────────────────────────────────────────────────────────────────┘   │
 │                          SQLite (synthetic data)                     │
 └──────────────────────────────────────────────────────────────────────┘
```

### Core flow

```
Detect → Diagnose → Generate recovery options
        → Simulate / evaluate → Optimize → Guardrails
        → Execute (simulated) → Verify → Audit
```

| Stage | Module | What it does |
| --- | --- | --- |
| Detect | payment data layer | Holds the universe of failed payments and their facts. |
| Diagnose | `diagnosis_service` | Produces a `DiagnosisResult`: failure category, likely cause, confidence, explanation, risk / timing / customer observations, eligible strategies. |
| Generate | `eligibility.EligibilityEngine` | Decides which of the canonical strategies (`retry_now`, `retry_later`, `payment_link`, `alternate_payment_method`, `customer_reminder`, `human_escalation`, `stop`) are even allowed. |
| Simulate | `strategy_simulator` | For every eligible strategy, returns a `SimulatedStrategyOutcome` with success probability, expected recovery, friction, time-to-recovery, and rationale. |
| Optimize | `optimizer.ExpectedRecoveryOptimizer` | Ranks outcomes by expected recovered amount and surfaces the top selection with a selection reason. |
| Guardrails | `guardrails.RecoveryGuardrails` | Deterministic policy layer that *allows or blocks* the selected strategy against merchant limits. |
| Execute | `simulated_executor.SimulatedRecoveryExecutor` | Simulates the outcome. Never touches a real payment API. |
| Verify / Audit | `repositories` | Persists `recovery_attempts`, `audit_logs`, and an immutable `decision_snapshots` row capturing the original decision. |

---

## Safety and guardrails

PayFix is a **simulation-only** product. The guardrail layer is the final
authority on whether any strategy is allowed to execute, and is enforced
deterministically by the backend (`backend/app/guardrails.py`).

| Guardrail | Value | Where it's enforced |
| --- | --- | --- |
| Maximum automated retries per payment | **2** | `GuardrailConfig.max_automated_retries` |
| Maximum automated customer contacts per payment | **2** | `GuardrailConfig.max_automated_customer_contacts` |
| Maximum automated payment amount | **₹10,000** | `GuardrailConfig.max_automated_payment_amount` |
| Suspected-fraud / high-risk protection | **Enabled** — automatic recovery is blocked | `GuardrailConfig.block_high_risk_automation` |
| Permanent-decline retry block | `permanent_decline`, `expired_card`, `expired_payment_method` are never auto-retried | `eligibility.RETRY_BLOCKED_CATEGORIES` |
| Safe fallback — human escalation | Always permitted for manual review | `RecoveryGuardrails.evaluate` |
| Safe fallback — stop | Always allowed as a no-op | `RecoveryGuardrails.evaluate` |

Every guardrail decision includes an explicit `reason` and a list of
`checks` describing what was evaluated. These are surfaced verbatim on the
Guardrails frontend page and the payment-detail page.

**Available payment methods and eligibility are authoritative.**
The AI is not permitted to invent unavailable methods; if a strategy is not
eligible for a payment, it is not even considered for optimization.

**No real money movement, ever.** The executor is a pure simulator. There
is no code path that calls a real payment gateway or sends a real
notification to a customer.

---

## Technology stack

| Layer | Technology |
| --- | --- |
| Backend language | Python 3.10+ |
| Backend framework | FastAPI |
| Database | SQLite (`backend/data/payfix.db`, configurable via `PAYFIX_DATABASE_PATH`) |
| Data + numerics | Pandas / NumPy for batch evaluation and metrics |
| Frontend language | JavaScript (ES2022+) |
| Frontend framework | React 18 |
| Build tool | Vite |
| Testing | `unittest` (Python) |

---

## Project layout

```
PayFix/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI routes
│   │   ├── diagnosis_service.py     # AI diagnosis engine
│   │   ├── diagnosis_models.py
│   │   ├── eligibility.py           # Deterministic strategy eligibility
│   │   ├── guardrails.py            # Deterministic policy layer
│   │   ├── strategy_simulator.py    # Strategy Simulator
│   │   ├── optimizer.py             # Expected Recovery Optimizer
│   │   ├── recovery_service.py      # End-to-end recovery orchestration
│   │   ├── recovery_models.py
│   │   ├── simulated_executor.py    # Simulated execution (no real money)
│   │   ├── decision_service.py      # Read-only decision snapshot service
│   │   ├── evaluation.py            # Batch evaluation engine
│   │   ├── demo_data.py             # 6 representative demo payments
│   │   ├── synthetic_data.py        # 500-payment synthetic batch
│   │   ├── repositories.py          # SQLite access
│   │   ├── models.py                # Domain models
│   │   └── database.py              # Schema + init
│   ├── tests/                       # 116 unit + integration tests
│   ├── data/                        # payfix.db lives here
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx                  # Sidebar + view router
│   │   ├── Dashboard.jsx            # KPIs, funnel, strategy mix, cases
│   │   ├── RecoveryQueue.jsx        # Queue listing (read-only)
│   │   ├── AIDecisions.jsx          # AI decision center (read-only)
│   │   ├── Guardrails.jsx           # Guardrail pipeline + policy view
│   │   ├── PaymentDetail.jsx        # Per-payment decision inspection
│   │   ├── api.js                   # Backend client + representative IDs
│   │   ├── format.js                # Formatting helpers
│   │   ├── Icon.jsx                 # Inline SVG icon set
│   │   └── style.css                # PayFix design system
│   ├── package.json
│   └── vite.config.js
└── README.md
```

---

## Frontend pages

All four operational pages are implemented and use only **read-only** API
calls — opening or refreshing any page never mutates the database.

| Page | Purpose | API used |
| --- | --- | --- |
| **Dashboard** | KPIs, recovery funnel, strategy mix, representative cases | `POST /evaluation/run`, `GET /payments/{id}/decision` |
| **Recovery Queue** | Representative cases enriched with display-only metadata | `GET /payments/{id}/decision` |
| **AI Decisions** | Per-payment decision center — diagnosis, eligible strategies, ranked outcomes, guardrails, execution | `GET /payments/{id}/decision` |
| **Guardrails** | Merchant safety limits, guardrail pipeline, per-payment guardrail decisions and individual checks | `GET /payments/{id}/decision` |
| **Payment Detail** | Full per-payment decision inspection (drill-in from any of the above) | `GET /payments/{id}/decision` |

The sidebar also surfaces a live "Backend health" pill and a persistent
"Simulation mode — no real payments processed" footer.

---

## API surface

All endpoints are JSON. Endpoints that *evaluate* a payment (`/diagnose`,
`/optimize`) are non-mutating. The single mutating endpoint is
`POST /payments/{id}/recover`, which is **idempotent** and reads back the
already-persisted decision on subsequent calls. To inspect a decision
without executing it, use the read-only `GET /payments/{id}/decision`.

| Method | Path | Description | Mutates? |
| --- | --- | --- | --- |
| `GET`  | `/health` | Liveness probe. Returns `{"status":"ok","service":"payfix"}`. | No |
| `POST` | `/payments/{payment_id}/diagnose` | Run the AI diagnosis engine for a single failed payment. | No |
| `POST` | `/payments/{payment_id}/optimize` | Run the deterministic eligibility engine, the strategy simulator, and the expected-recovery optimizer. | No |
| `POST` | `/payments/{payment_id}/recover` | Run the full pipeline (diagnose → optimize → guardrails → execute) and persist the decision snapshot. Idempotent. | **Yes** (idempotent) |
| `GET`  | `/payments/{payment_id}/decision` | Read-only inspection of the *original* persisted decision snapshot for a payment. Never executes recovery. | No |
| `POST` | `/evaluation/run` | Run batch evaluation over a synthetic payment batch and return aggregate metrics + strategy mix. | No |

---

## Latest evaluation results

The latest run of `POST /evaluation/run` over the 500-payment synthetic batch:

| Metric | Value |
| --- | --- |
| Synthetic payments generated | 500 |
| Failed payments evaluated | 293 |
| Revenue at risk | ₹30,55,825 |
| PayFix recovered | ₹5,07,160 |
| Successful recoveries | 108 |
| Human escalations | 104 |
| PayFix recovery rate | 16.6% |
| Naive retry baseline recovered | ₹0 |

PayFix is being compared to a deliberately naive "just keep retrying"
baseline, not to a real-world production system. The baseline of ₹0 reflects
the fact that the synthetic batch is constructed to be resistant to blind
retries (permanent declines, expired cards, and suspected-fraud payments
are over-represented); PayFix outperforms it by routing each payment to
the right strategy and refusing unsafe actions.

---

## Buildathon track

- **Track:** Track 03 — AI Revenue Recovery
- **Pitch:** "Find revenue that's slipping away and win it back."
- **Constraints honored:** all payment data and recovery outcomes are
  synthetic and generated for the buildathon. No real payment APIs, no
  real customer data, no real money movement, no real notifications.

---

## Setup & run

### Prerequisites

- Python 3.10 or newer
- Node.js 20 or newer (includes npm)

### 1. Backend

From the `backend` directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

By default the database is created at `backend/data/payfix.db`. Set
`PAYFIX_DATABASE_PATH` to use another local path.

Verify it's up:

```powershell
curl http://127.0.0.1:8000/health
# {"status":"ok","service":"payfix"}
```

Seed the demo data (idempotent):

```powershell
python -c "from app.demo_data import load_demo_data; print(f'Inserted {load_demo_data()} demo payments')"
```

Run the full backend test suite (116 tests):

```powershell
python -m unittest discover -s tests -v
```

### 2. Frontend

From the `frontend` directory:

```powershell
npm install
npm run dev
```

Open the local URL printed by Vite (normally `http://localhost:5173`).

Production build:

```powershell
npm run build
```

The Vite build output is written to `frontend/dist/`.

---

## What PayFix does **not** claim

- PayFix **does not** process real payments.
- PayFix **does not** move real money.
- PayFix **does not** send real customer communications.
- PayFix **does not** guarantee any specific real-world recovery rate.
  Numbers shown in the UI and in this README are from synthetic batch
  evaluation and are intended for buildathon demonstration only.
- PayFix **does not** invent unavailable payment methods or strategies.
  The eligibility engine is the authority on what is even considered.
