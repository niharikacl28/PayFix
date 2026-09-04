"""PayFix FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .database import initialize_database
from .diagnosis_service import DiagnosisService, PaymentNotFoundError
from .evaluation import BatchEvaluator
from .optimizer import ExpectedRecoveryOptimizer
from .recovery_service import RecoveryService
from .repositories import PayFixRepository


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create the local persistent store before accepting requests."""
    initialize_database()
    yield


app = FastAPI(title="PayFix API", lifespan=lifespan)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return the service health status."""
    return {"status": "ok", "service": "payfix"}


@app.post("/payments/{payment_id}/diagnose")
def diagnose_payment(payment_id: str) -> dict[str, object]:
    """Explain a failed payment and return only backend-approved strategy names."""
    try:
        result = DiagnosisService(PayFixRepository()).diagnose_payment(payment_id)
    except PaymentNotFoundError as error:
        raise HTTPException(status_code=404, detail="Payment not found.") from error
    return result.to_dict()


@app.post("/payments/{payment_id}/optimize")
def optimize_payment(payment_id: str) -> dict[str, object]:
    """Rank synthetic outcomes for eligible strategies; no recovery action is executed."""
    try:
        result = ExpectedRecoveryOptimizer(PayFixRepository()).optimize_payment(payment_id)
    except PaymentNotFoundError as error:
        raise HTTPException(status_code=404, detail="Payment not found.") from error
    return result.to_dict()


@app.post("/payments/{payment_id}/recover")
def recover_payment(payment_id: str) -> dict[str, object]:
    """Run a bounded, fully synthetic recovery workflow; no real payment action is taken."""
    try:
        result = RecoveryService(PayFixRepository()).recover_payment(payment_id)
    except PaymentNotFoundError as error:
        raise HTTPException(status_code=404, detail="Payment not found.") from error
    return result.to_dict()


@app.post("/evaluation/run")
def run_evaluation(batch_size: int = 100, payment_ids: list[str] | None = None) -> dict[str, object]:
    """Run batch evaluation comparing PayFix recovery against a deterministic baseline.

    Args:
        batch_size: Maximum number of payments to evaluate (default: 100)
        payment_ids: Optional list of specific payment IDs to evaluate

    Returns:
        Dictionary containing evaluation results with metrics
    """
    try:
        result = BatchEvaluator(PayFixRepository()).run_evaluation(
            batch_size=batch_size, payment_ids=payment_ids
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return result
