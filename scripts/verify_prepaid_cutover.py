#!/usr/bin/env python3
"""
Prepaid Billing Cutover Verification Script (PREPAID_BILLING_PLAN.md §5.2)

READ-ONLY. There is no --commit mode and this script must never gain one.

What must NOT be done here — stated loudly because a future reader will be
tempted to "fix" a nonzero balance by backfilling history:

  * Do NOT backfill the minute_ledger from historical calls.duration_seconds.
    Every org would start deeply negative and be instantly blocked, and
    clients would be charged twice for work already invoiced under postpaid.
    The ledger begins at cutover. Full stop.
  * Do NOT convert unpaid legacy billing_snapshots into negative balance.
    Same double-charge reason. Money owed under postpaid stays owed under
    postpaid, collected out-of-band, visible read-only in Past Invoices.
  * Historical usage stays where it is. daily_usage_metrics continues to be
    the source for the Usage tab and is unaffected by prepaid.

Pre-flight checks (exits non-zero on any failure):
  1. No call is 'pending' or 'transcribing'.
  2. Schema has applied: both prepaid_recharges/minute_ledger tables exist,
     and both minute_grace_limit/infra_grace_days columns exist on organizations.
  3. minute_ledger is empty.

Report: prints a table of every organization (id, name, per_minute_cost,
infra_fixed_cost, minute_grace_limit, computed state). Every row should read
'blocked' — this is the live proof that the "grace is earned, not granted"
clause in §2.4 works: a zero-balance org with no recharge history is blocked,
not sitting in a free grace window.
"""

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def run_preflight_and_report(db_path: str = None) -> int:
    """Returns 0 on success, non-zero on any pre-flight failure."""
    if db_path:
        os.environ["DATABASE_PATH"] = str(Path(db_path).resolve())

    # Imported after DATABASE_PATH is set so DatabaseManager resolves the right file.
    from src.app.models.base import DatabaseManager
    from src.app.models.prepaid import Prepaid

    failures = []

    with DatabaseManager.get_connection() as conn:
        cursor = conn.cursor()

        # 1. No call pending/transcribing.
        cursor.execute("SELECT COUNT(*) AS cnt FROM calls WHERE processing_status IN ('pending', 'transcribing');")
        in_flight = cursor.fetchone()["cnt"]
        if in_flight > 0:
            failures.append(f"{in_flight} call(s) are still 'pending'/'transcribing'. Queue must be empty before cutover.")

        # 2. Schema applied: both new tables.
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('prepaid_recharges', 'minute_ledger');")
        found_tables = {r["name"] for r in cursor.fetchall()}
        for tbl in ("prepaid_recharges", "minute_ledger"):
            if tbl not in found_tables:
                failures.append(f"Schema not applied: table '{tbl}' does not exist.")

        # 2b. Schema applied: both new org columns.
        cursor.execute("PRAGMA table_info(organizations);")
        org_cols = {r["name"] for r in cursor.fetchall()}
        for col in ("minute_grace_limit", "infra_grace_days"):
            if col not in org_cols:
                failures.append(f"Schema not applied: organizations.{col} column does not exist.")

        # 3. minute_ledger must be empty (nothing has been backfilled/written yet).
        if "minute_ledger" in found_tables:
            cursor.execute("SELECT COUNT(*) AS cnt FROM minute_ledger;")
            ledger_count = cursor.fetchone()["cnt"]
            if ledger_count > 0:
                failures.append(f"minute_ledger is not empty ({ledger_count} row(s)). Cutover expects zero prior ledger history.")

        if failures:
            return failures, None

        # Report: every organization's computed state.
        cursor.execute("SELECT id, name, per_minute_cost, infra_fixed_cost, minute_grace_limit, infra_grace_days FROM organizations ORDER BY id ASC;")
        orgs = [dict(r) for r in cursor.fetchall()]

    report_rows = []
    for org in orgs:
        state_info = Prepaid.get_state(org["id"], float(org["minute_grace_limit"]), int(org["infra_grace_days"]))
        report_rows.append({
            "id": org["id"],
            "name": org["name"],
            "per_minute_cost": org["per_minute_cost"],
            "infra_fixed_cost": org["infra_fixed_cost"],
            "minute_grace_limit": org["minute_grace_limit"],
            "state": state_info["state"],
        })

    return [], report_rows


def print_report(report_rows) -> None:
    header = f"{'id':>4}  {'name':<30}  {'per_min':>10}  {'infra_fixed':>12}  {'grace_limit':>12}  {'state':<10}"
    print(header)
    print("-" * len(header))
    non_blocked = []
    for row in report_rows:
        print(f"{row['id']:>4}  {row['name'][:30]:<30}  {row['per_minute_cost']:>10.2f}  {row['infra_fixed_cost']:>12.2f}  {row['minute_grace_limit']:>12.2f}  {row['state']:<10}")
        if row["state"] != "blocked":
            non_blocked.append(row["id"])

    print("-" * len(header))
    print(f"Total organizations: {len(report_rows)}")
    if non_blocked:
        print(f"WARNING: {len(non_blocked)} organization(s) are NOT 'blocked' (ids: {non_blocked}). "
              f"This means a recharge already exists — expected only if this check is run post-recharge.")
    else:
        print("All organizations report 'blocked' as expected for a pre-recharge cutover.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only prepaid billing cutover verification (no write path).")
    parser.add_argument("--db-path", type=str, help="Override database file path")
    args = parser.parse_args()

    failures, report_rows = run_preflight_and_report(db_path=args.db_path)

    if failures:
        print("PRE-FLIGHT CHECK FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        sys.exit(1)

    print("Pre-flight checks passed:")
    print("  - No call is pending/transcribing.")
    print("  - Schema applied (prepaid_recharges, minute_ledger, organizations.minute_grace_limit/infra_grace_days).")
    print("  - minute_ledger is empty.")
    print()
    print_report(report_rows)
    sys.exit(0)


if __name__ == "__main__":
    main()
