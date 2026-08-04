import sqlite3
import datetime
from typing import Optional, List, Dict, Any
from src.app.models.base import DatabaseManager
from src.app.core.logging_config import get_logger

logger = get_logger(__name__)


def _immediate_connection() -> sqlite3.Connection:
    """
    Opens a dedicated connection with manual transaction control so a single
    BEGIN IMMEDIATE can wrap a balance-read + ledger-insert atomically. Using
    DatabaseManager.get_connection() is not sufficient here because its
    implicit ("") isolation level only opens a DEFERRED transaction lazily on
    the first write, which does not guard the SELECT that precedes it.
    """
    conn = sqlite3.connect(
        DatabaseManager.get_db_path(),
        timeout=30.0,
        isolation_level=None,  # autocommit off; we drive transactions manually
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


class Prepaid:
    """
    Prepaid billing: recharges (infra / minute packs) and the append-only
    minute ledger. Balance is always derived as SUM(minute_ledger.minutes_delta)
    — never stored on the org row. See PREPAID_BILLING_PLAN.md §2.3/§2.4.
    """

    # ------------------------------------------------------------------
    # Recharges
    # ------------------------------------------------------------------

    @staticmethod
    def create_recharge(
        organization_id: int,
        recharge_type: str,
        unit_price_at_purchase: float,
        amount_charged: float,
        minutes_purchased: Optional[float] = None,
        months_purchased: Optional[int] = None,
        infra_period_start: Optional[str] = None,
        infra_period_end: Optional[str] = None,
        currency: str = "INR",
        payment_provider: str = "manual",
        payment_reference: Optional[str] = None,
        payment_status: str = "paid",
        paid_at: Optional[str] = None,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Inserts the recharge row. If it is a 'minutes' recharge that is paid
        (and not voided), also credits the ledger in the same BEGIN IMMEDIATE
        transaction. Returns {"id": ..., "new_minute_balance": ...}.
        """
        conn = _immediate_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO prepaid_recharges (
                    organization_id, recharge_type, minutes_purchased, months_purchased,
                    infra_period_start, infra_period_end, unit_price_at_purchase, amount_charged,
                    currency, payment_provider, payment_reference, payment_status, paid_at,
                    notes, created_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    organization_id, recharge_type, minutes_purchased, months_purchased,
                    infra_period_start, infra_period_end, unit_price_at_purchase, amount_charged,
                    currency, payment_provider, payment_reference, payment_status, paid_at,
                    notes, created_by_user_id,
                ),
            )
            recharge_id = cursor.lastrowid

            new_balance = None
            if recharge_type == "minutes" and payment_status == "paid" and minutes_purchased:
                new_balance = Prepaid._credit_minutes_locked(
                    conn, organization_id, float(minutes_purchased), recharge_id,
                    note=f"Recharge #{recharge_id}", created_by_user_id=created_by_user_id
                )
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(minutes_delta), 0.0) AS bal FROM minute_ledger WHERE organization_id = ?;",
                    (organization_id,),
                ).fetchone()
                new_balance = round(float(row["bal"] or 0.0), 2)

            conn.commit()
            return {"id": recharge_id, "new_minute_balance": new_balance}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def list_recharges(
        organization_id: int,
        recharge_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        query = "SELECT * FROM prepaid_recharges WHERE organization_id = ?"
        params: List[Any] = [organization_id]
        if recharge_type:
            query += " AND recharge_type = ?"
            params.append(recharge_type)

        count_query = query.replace("SELECT *", "SELECT COUNT(*) as cnt")
        total_rows = DatabaseManager.execute_query(count_query, tuple(params))
        total = total_rows[0]["cnt"] if total_rows else 0

        query += " ORDER BY id DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])
        rows = DatabaseManager.execute_query(query, tuple(params))
        return {"recharges": rows, "total": total}

    @staticmethod
    def get_recharge(recharge_id: int) -> Optional[sqlite3.Row]:
        rows = DatabaseManager.execute_query("SELECT * FROM prepaid_recharges WHERE id = ?;", (recharge_id,))
        return rows[0] if rows else None

    @staticmethod
    def void_recharge(recharge_id: int, reason: str, voided_by_user_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Marks a recharge voided and writes a compensating 'void' ledger entry
        (only if the original recharge actually credited minutes). Never
        deletes or mutates the original recharge row's amounts. Voiding an
        already-voided recharge is rejected, not double-reversed.
        """
        conn = _immediate_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.cursor()
            row = cursor.execute("SELECT * FROM prepaid_recharges WHERE id = ?;", (recharge_id,)).fetchone()
            if row is None:
                conn.rollback()
                return {"status": "not_found"}

            if row["voided_at"] is not None:
                conn.rollback()
                return {"status": "already_voided"}

            cursor.execute(
                "UPDATE prepaid_recharges SET voided_at = CURRENT_TIMESTAMP, voided_by_user_id = ?, notes = COALESCE(notes, '') || ? WHERE id = ?;",
                (voided_by_user_id, f"\n[VOIDED] {reason}", recharge_id),
            )

            reversal_ledger_id = None
            organization_id = row["organization_id"]
            if row["recharge_type"] == "minutes" and row["payment_status"] == "paid" and row["minutes_purchased"]:
                # Only reverse if a credit was actually ever written for this recharge.
                existing_credit = cursor.execute(
                    "SELECT id FROM minute_ledger WHERE recharge_id = ? AND entry_type = 'recharge';",
                    (recharge_id,),
                ).fetchone()
                if existing_credit is not None:
                    bal_row = cursor.execute(
                        "SELECT COALESCE(SUM(minutes_delta), 0.0) AS bal FROM minute_ledger WHERE organization_id = ?;",
                        (organization_id,),
                    ).fetchone()
                    current_balance = float(bal_row["bal"] or 0.0)
                    minutes_delta = -float(row["minutes_purchased"])
                    balance_after = round(current_balance + minutes_delta, 2)
                    cursor.execute(
                        """
                        INSERT INTO minute_ledger (
                            organization_id, entry_type, minutes_delta, balance_after,
                            recharge_id, note, created_by_user_id
                        ) VALUES (?, 'void', ?, ?, ?, ?, ?);
                        """,
                        (organization_id, minutes_delta, balance_after, recharge_id, f"Void of recharge #{recharge_id}: {reason}", voided_by_user_id),
                    )
                    reversal_ledger_id = cursor.lastrowid

            bal_row = cursor.execute(
                "SELECT COALESCE(SUM(minutes_delta), 0.0) AS bal FROM minute_ledger WHERE organization_id = ?;",
                (organization_id,),
            ).fetchone()
            new_balance = round(float(bal_row["bal"] or 0.0), 2)

            conn.commit()
            return {
                "status": "success",
                "reversal_ledger_id": reversal_ledger_id,
                "new_minute_balance": new_balance,
                "organization_id": organization_id,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Ledger writes (idempotent, BEGIN IMMEDIATE)
    # ------------------------------------------------------------------

    @staticmethod
    def _credit_minutes_locked(conn: sqlite3.Connection, organization_id: int, minutes: float,
                                recharge_id: int, note: Optional[str] = None,
                                created_by_user_id: Optional[int] = None) -> float:
        """Must be called with an already-open BEGIN IMMEDIATE transaction on `conn`."""
        cursor = conn.cursor()
        bal_row = cursor.execute(
            "SELECT COALESCE(SUM(minutes_delta), 0.0) AS bal FROM minute_ledger WHERE organization_id = ?;",
            (organization_id,),
        ).fetchone()
        current_balance = float(bal_row["bal"] or 0.0)
        balance_after = round(current_balance + minutes, 2)
        cursor.execute(
            """
            INSERT INTO minute_ledger (
                organization_id, entry_type, minutes_delta, balance_after,
                recharge_id, note, created_by_user_id
            ) VALUES (?, 'recharge', ?, ?, ?, ?, ?);
            """,
            (organization_id, minutes, balance_after, recharge_id, note, created_by_user_id),
        )
        return balance_after

    @staticmethod
    def credit_minutes(organization_id: int, minutes: float, recharge_id: int,
                        note: Optional[str] = None, created_by_user_id: Optional[int] = None) -> Optional[float]:
        """
        Standalone credit path (used outside create_recharge). Idempotent via
        idx_ledger_recharge_credit — a duplicate call for the same recharge_id
        is caught, logged, and no-ops rather than raising.
        """
        conn = _immediate_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            new_balance = Prepaid._credit_minutes_locked(conn, organization_id, minutes, recharge_id, note, created_by_user_id)
            conn.commit()
            return new_balance
        except sqlite3.IntegrityError as e:
            conn.rollback()
            logger.info(f"Prepaid.credit_minutes: duplicate credit for recharge_id={recharge_id} ignored (idempotent no-op): {e}")
            return None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def debit_call(organization_id: int, call_id: int, minutes: float) -> Optional[float]:
        """
        Debits minutes for a completed call. Idempotent via idx_ledger_call_usage
        — a duplicate call_id debit is caught, logged, and no-ops rather than
        raising or double-charging.
        """
        conn = _immediate_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.cursor()
            bal_row = cursor.execute(
                "SELECT COALESCE(SUM(minutes_delta), 0.0) AS bal FROM minute_ledger WHERE organization_id = ?;",
                (organization_id,),
            ).fetchone()
            current_balance = float(bal_row["bal"] or 0.0)
            minutes_delta = -abs(float(minutes))
            balance_after = round(current_balance + minutes_delta, 2)
            cursor.execute(
                """
                INSERT INTO minute_ledger (
                    organization_id, entry_type, minutes_delta, balance_after, call_id, note
                ) VALUES (?, 'usage', ?, ?, ?, ?);
                """,
                (organization_id, minutes_delta, balance_after, call_id, f"Usage debit for call #{call_id}"),
            )
            conn.commit()
            return balance_after
        except sqlite3.IntegrityError as e:
            conn.rollback()
            logger.info(f"Prepaid.debit_call: duplicate debit for call_id={call_id} ignored (idempotent no-op): {e}")
            return None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Read-side: balance / state
    # ------------------------------------------------------------------

    @staticmethod
    def get_balance(organization_id: int) -> float:
        rows = DatabaseManager.execute_query(
            "SELECT COALESCE(SUM(minutes_delta), 0.0) AS bal FROM minute_ledger WHERE organization_id = ?;",
            (organization_id,),
        )
        if not rows:
            return 0.0
        return round(float(rows[0]["bal"] or 0.0), 2)

    @staticmethod
    def get_infra_valid_until(organization_id: int) -> Optional[str]:
        rows = DatabaseManager.execute_query(
            """
            SELECT MAX(infra_period_end) AS valid_until FROM prepaid_recharges
            WHERE organization_id = ? AND recharge_type = 'infra'
              AND payment_status = 'paid' AND voided_at IS NULL;
            """,
            (organization_id,),
        )
        if not rows or rows[0]["valid_until"] is None:
            return None
        return rows[0]["valid_until"]

    @staticmethod
    def has_ever_recharged(organization_id: int) -> bool:
        rows = DatabaseManager.execute_query(
            """
            SELECT 1 FROM prepaid_recharges
            WHERE organization_id = ? AND payment_status = 'paid' AND voided_at IS NULL
            LIMIT 1;
            """,
            (organization_id,),
        )
        return len(rows) > 0

    @staticmethod
    def get_state(organization_id: int, minute_grace_limit: float, infra_grace_days: int) -> Dict[str, Any]:
        """
        Implements the §2.4 state machine. "Grace is earned, not granted": an
        org with zero paid, non-voided recharge history is `blocked`, never
        `grace`, even though a zero balance would otherwise satisfy the grace
        condition (-grace_limit < 0 <= 0). This is what makes the D3 zero-balance
        cutover behave as "blocked until paid" instead of a free grace window.
        """
        balance = Prepaid.get_balance(organization_id)
        infra_valid_until = Prepaid.get_infra_valid_until(organization_id)
        ever_recharged = Prepaid.has_ever_recharged(organization_id)

        today = datetime.date.today()
        infra_expired = False
        infra_days_past_expiry = 0
        if infra_valid_until is not None:
            try:
                valid_until_date = datetime.date.fromisoformat(str(infra_valid_until)[:10])
                if today > valid_until_date:
                    infra_expired = True
                    infra_days_past_expiry = (today - valid_until_date).days
            except ValueError:
                pass

        if not ever_recharged:
            state = "blocked"
            blocked_reason = "No prepaid recharge has ever been recorded for this organization."
        elif balance <= -minute_grace_limit:
            state = "blocked"
            blocked_reason = f"Minute balance ({balance}) has reached the grace floor (-{minute_grace_limit})."
        elif infra_expired and infra_days_past_expiry > infra_grace_days:
            state = "blocked"
            blocked_reason = f"Infra validity expired {infra_days_past_expiry} day(s) ago, beyond the {infra_grace_days}-day grace period."
        elif balance <= 0 or (infra_expired and infra_days_past_expiry <= infra_grace_days):
            state = "grace"
            blocked_reason = None
        else:
            # balance > 0 and infra is either not expired or was never purchased at all.
            # NOTE (deviation from the literal §2.4 "ok" row): the plan's table reads
            # `today <= infra_valid_until`, which is undefined when no infra recharge
            # has ever been made (infra_valid_until IS NULL). Infra and minute packs
            # are independent, optional products per §2.1/§2.2 — an org that has only
            # ever bought minute packs must still be able to reach `ok`. Treating "no
            # infra period ever purchased" as vacuously satisfying the infra clause
            # (rather than perpetually blocking/grace-ing a minutes-only org) is the
            # only reading consistent with the rest of the design. Flagged in the report.
            state = "ok"
            blocked_reason = None

        return {
            "state": state,
            "blocked_reason": blocked_reason,
            "minute_balance": balance,
            "infra_valid_until": infra_valid_until,
        }

    # ------------------------------------------------------------------
    # Ledger listing
    # ------------------------------------------------------------------

    @staticmethod
    def list_ledger(
        organization_id: int,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        entry_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        query = "SELECT * FROM minute_ledger WHERE organization_id = ?"
        params: List[Any] = [organization_id]
        if start_date:
            query += " AND date(created_at) >= date(?)"
            params.append(start_date)
        if end_date:
            query += " AND date(created_at) <= date(?)"
            params.append(end_date)
        if entry_type:
            query += " AND entry_type = ?"
            params.append(entry_type)

        count_query = query.replace("SELECT *", "SELECT COUNT(*) as cnt")
        total_rows = DatabaseManager.execute_query(count_query, tuple(params))
        total = total_rows[0]["cnt"] if total_rows else 0

        query += " ORDER BY id DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])
        rows = DatabaseManager.execute_query(query, tuple(params))
        return {"entries": rows, "total": total, "minute_balance": Prepaid.get_balance(organization_id)}
