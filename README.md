# PayFix

PayFix is a buildathon prototype for safe, simulated payment-recovery workflows. It includes a FastAPI health endpoint, a React + Vite frontend, and a local SQLite data layer for future recovery workflows.

No real payment APIs, real customer data, payment execution, guardrails, or dashboard are included yet. The included failed payments and simulator estimates are entirely synthetic.

## Backend

Prerequisite: Python 3.10 or newer.

From the `backend` directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000/health`. It returns:

```json
{"status":"ok","service":"payfix"}
```

### SQLite database

The application initializes its SQLite schema automatically on FastAPI startup. By default, the database file is `backend/data/payfix.db`; set `PAYFIX_DATABASE_PATH` to use another local path.

Load the synthetic failed-payment examples from `backend` after activating the environment:

```powershell
python -c "from app.demo_data import load_demo_data; print(f'Inserted {load_demo_data()} demo payments')"
```

The seed covers temporary network failures, insufficient funds, expired cards, permanent declines, suspected fraud, and a recurring mandate failure. It stores facts and available payment methods only; it makes no recovery decisions.

### Recovery strategy eligibility

`app/strategies.py` defines the only recovery actions PayFix may consider. `app/eligibility.py` deterministically evaluates every strategy against payment facts and returns an eligible flag, reason, and metadata. Default retry, contact, and automated-amount limits are centralized in `app/recovery_config.py`; no LLM, simulator, or payment execution is involved.

### AI Diagnosis Engine

`POST /payments/{payment_id}/diagnose` explains stored failed-payment facts and returns a structured diagnosis plus strategy names permitted by the deterministic eligibility engine. It does not select an action, execute a payment, estimate recovery, or override eligibility.

The default `mock` provider is deterministic, offline, and used by tests. Set `PAYFIX_DIAGNOSIS_PROVIDER=openai`, `PAYFIX_OPENAI_API_KEY`, and optionally `PAYFIX_OPENAI_MODEL` to enable the optional OpenAI provider. Keep keys in environment variables; never commit them.

Example response:

```json
{
  "payment_id": "pay_demo_network",
  "failure_category": "temporary_failure",
  "confidence": 0.9,
  "likely_cause": "A temporary bank, issuer, or network interruption is likely.",
  "eligible_strategy_names": ["retry_now", "retry_later", "payment_link", "customer_reminder", "stop"]
}
```

### Strategy Simulator and Optimizer

`POST /payments/{payment_id}/optimize` simulates only strategies that the deterministic eligibility engine has allowed. It ranks them by synthetic expected recovery value and never executes the selected strategy.

Expected recovered amount is calculated transparently as `payment amount × success probability`. The fixed, deterministic probability model starts with a strategy baseline, then applies documented adjustments for the diagnosed failure category, recorded customer history, retry count, contact fatigue, and—for `retry_later`—a timing/drop-off adjustment. These estimates are comparison aids, not real-world predictions or guarantees.

Every simulated outcome shows its friction level (`none` through `high`), estimated time to recovery, rationale, and assumptions. The optimizer always includes `stop`; it ranks eligible outcomes by expected recovered amount and exposes the top result for a future decision layer to review.

Example response excerpt:

```json
{
  "selected_strategy": "retry_now",
  "expected_recovered_amount": 6000.0,
  "simulation_version": "synthetic-v1",
  "ranked_strategies": [
    {
      "strategy": "retry_now",
      "success_probability": 0.75,
      "estimated_customer_friction": "low",
      "estimated_time_to_recovery": "immediate"
    }
  ]
}
```

### Tests

From `backend`, with the virtual environment active:

```powershell
python -m unittest discover -s tests -v
```

## Frontend

Prerequisite: Node.js 20 or newer (includes npm).

From the `frontend` directory:

```powershell
npm install
npm run dev
```

Open the local URL printed by Vite, normally `http://localhost:5173`.
