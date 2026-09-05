"""One-time local reset of the six demo payment records.

PURPOSE
-------
Restore the six ``pay_demo_*`` payment rows to their original unprocessed
demo state so they can be executed once through the real
``POST /payments/{id}/recover`` endpoint and generate fresh immutable
``decision_snapshots`` rows via the unmodified ``RecoveryService``.

This is a LOCAL one-off script. It does NOT modify any production
application code. It does NOT drop or recreate the database. It does
NOT touch any ``pay_syn_*`` synthetic payment. It does NOT manually
insert any recovery_attempts, audit_logs, or decision_snapshots.

The deletion is intentionally limited to:
    - rows in  decision_snapshots  whose payment_id is one of the six
    - rows in  audit_logs          whose payment_id is one of the six
    - rows in  recovery_attempts   whose payment_id is one of the six
    - rows in  payments            whose payment_id is one of the six

After the surgical delete, the existing ``app.demo_data.load_demo_data()``
is invoked to re-insert the six original ``Payment`` objects as defined
in the production ``demo_data.py`` module.

USAGE
-----
    cd backend
    python scripts/reset_demo_payments.py
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = BACKEND_DIR / "data" / "payfix.db"

# The six representative demo payment IDs as defined in app.demo_data.
DEMO_PAYMENT_IDS: tuple[str, ...] = (
    "pay_demo_network",
    "pay_demo_funds",
    "pay_demo_expired",
    "pay_demo_decline",
    "pay_demo_fraud",
    "pay_demo_subscription",
)

# Tables to clean. Order matters: child tables first, parent table last.
# decision_snapshots references payment_id, so it is deleted first.
# audit_logs and recovery_attempts also reference payment_id.
# payments is the parent and is deleted last.
CLEANUP_TABLES: tuple[str, ...] = (
    "decision_snapshots",
    "audit_logs",
    "recovery_attempts",
    "payments",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_rows(conn: sqlite3.Connection, table: str, ids: tuple[str, ...]) -> int:
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE payment_id IN ({placeholders})",  # nosec - parameterised
        ids,
    )
    return int(cur.fetchone()[0])


def _delete_rows(conn: sqlite3.Connection, table: str, ids: tuple[str, ...]) -> int:
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"DELETE FROM {table} WHERE payment_id IN ({placeholders})",  # nosec - parameterised
        ids,
    )
    return int(cur.rowcount)


def _count_syn_payments(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM payments WHERE payment_id LIKE 'pay_syn_%'"
        ).fetchone()[0]
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 72)
    print("PayFix demo-payment reset (one-time local script)")
    print("=" * 72)
    print(f"Database : {DATABASE_PATH}")
    print(f"Demo IDs : {', '.join(DEMO_PAYMENT_IDS)}")
    print()

    if not DATABASE_PATH.exists():
        print(f"ERROR: database not found at {DATABASE_PATH}", file=sys.stderr)
        return 2

    # 1) Pre-delete audit ---------------------------------------------------
    print("[1/5] Pre-delete counts for the six demo IDs:")
    with sqlite3.connect(DATABASE_PATH) as conn:
        # Foreign-key constraints must be ON for the deletion order to be
        # safe; SQLite requires this pragma per-connection.
        conn.execute("PRAGMA foreign_keys = ON;")
        pre_syn_count = _count_syn_payments(conn)
        pre_counts = {
            table: _count_rows(conn, table, DEMO_PAYMENT_IDS)
            for table in CLEANUP_TABLES
        }
    for table in CLEANUP_TABLES:
        print(f"        {table:>20s}: {pre_counts[table]} demo row(s)")
    print(f"        {'(synthetic pay_syn_*)':>20s}: {pre_syn_count} row(s)")
    print()

    # 2) Backup --------------------------------------------------------------
    print("[2/5] Creating timestamped backup...")
    backup_path = DATABASE_PATH.with_name(
        f"payfix.db.bak.{int(time.time())}"
    )
    shutil.copy2(DATABASE_PATH, backup_path)
    if not backup_path.exists():
        print(f"ERROR: backup not created at {backup_path}", file=sys.stderr)
        return 2
    backup_size = backup_path.stat().st_size
    print(f"        backup written : {backup_path}")
    print(f"        backup size    : {backup_size} bytes")
    print()

    # 3) Surgical delete ----------------------------------------------------
    print("[3/5] Deleting demo rows from child tables then parent table...")
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        deleted: dict[str, int] = {}
        for table in CLEANUP_TABLES:
            n = _delete_rows(conn, table, DEMO_PAYMENT_IDS)
            deleted[table] = n
            print(f"        {table:>20s}: deleted {n} row(s)")
        conn.commit()
    print()

    # 4) Re-insert via existing app.demo_data -------------------------------
    print("[4/5] Re-inserting six demo payments via app.demo_data.load_demo_data()...")
    # Defer the import until after the backup is in place, so a missing
    # or broken module does not destroy the backup.
    from app.demo_data import DEMO_PAYMENTS, load_demo_data  # noqa: WPS433

    inserted = load_demo_data(DATABASE_PATH)
    print(f"        load_demo_data() returned inserted = {inserted}")
    print(f"        DEMO_PAYMENTS has {len(DEMO_PAYMENTS)} entries (sanity check)")
    print()

    # 5) Post-insert verification -------------------------------------------
    print("[5/5] Post-reset verification:")
    with sqlite3.connect(DATABASE_PATH) as conn:
        post_syn_count = _count_syn_payments(conn)
        demo_rows = list(
            conn.execute(
                "SELECT payment_id, payment_status, recovery_status, "
                "recovered_amount, retry_count, customer_contact_count "
                "FROM payments WHERE payment_id IN ("
                + ",".join("?" for _ in DEMO_PAYMENT_IDS)
                + ") ORDER BY payment_id",
                DEMO_PAYMENT_IDS,
            )
        )
        post_snapshot_count = _count_rows(
            conn, "decision_snapshots", DEMO_PAYMENT_IDS
        )
        post_attempt_count = _count_rows(
            conn, "recovery_attempts", DEMO_PAYMENT_IDS
        )
        post_audit_count = _count_rows(conn, "audit_logs", DEMO_PAYMENT_IDS)

    print(f"        pay_syn_* row count: before={pre_syn_count}  after={post_syn_count}  "
          f"{'UNCHANGED' if pre_syn_count == post_syn_count else 'CHANGED <-- FAIL'}")
    print(f"        decision_snapshots rows for demo IDs: {post_snapshot_count} (expected 0)")
    print(f"        recovery_attempts  rows for demo IDs: {post_attempt_count}  (expected 0)")
    print(f"        audit_logs         rows for demo IDs: {post_audit_count} (expected 0)")
    print()
    print("        Demo payment row state:")
    header = (
        f"        {'payment_id':<22} {'payment_status':<14} "
        f"{'recovery_status':<18} {'recovered':>10} {'retry':>5} {'contact':>7}"
    )
    print(header)
    for row in demo_rows:
        pid, pstatus, rstatus, ramount, retry, contact = row
        print(
            f"        {pid:<22} {pstatus:<14} {rstatus:<18} "
            f"{str(ramount):>10} {retry:>5} {contact:>7}"
        )
    print()

    ok = (
        pre_syn_count == post_syn_count
        and post_snapshot_count == 0
        and post_attempt_count == 0
        and post_audit_count == 0
        and len(demo_rows) == len(DEMO_PAYMENT_IDS)
    )
    if ok:
        print("OK: six demo rows are present, fresh, and unprocessed.")
        print("    Back up at:", backup_path)
        print("    Next step : run the real POST /recover for each demo ID.")
    else:
        print("ERROR: post-reset verification failed; inspect database.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
