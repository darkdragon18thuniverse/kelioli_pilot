import sqlite3
from typing import List, Optional
from src.app.models.base import DatabaseManager


class Billing:
    """
    Handles immutable monthly accounting closures and time-series performance aggregations.
    """

    @staticmethod
    def create_snapshot(
        organization_id: int,
        tier_at_billing: str,
        infra_fixed_cost_charged: float,
        per_minute_cost_charged: float,
        total_minutes_consumed: float,
        total_spend_calculated: float,
        billing_period_start: str,
        billing_period_end: str,
        payment_status: str = "unpaid"
    ) -> int:
        """Creates an immutable billing snapshot record."""
        insert_query = """
            INSERT INTO billing_snapshots (
                organization_id, tier_at_billing, infra_fixed_cost_charged,
                per_minute_cost_charged, total_minutes_consumed, total_spend_calculated,
                billing_period_start, billing_period_end, payment_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            organization_id, tier_at_billing, infra_fixed_cost_charged,
            per_minute_cost_charged, total_minutes_consumed, total_spend_calculated,
            billing_period_start, billing_period_end, payment_status
        )
        return DatabaseManager.execute_update(insert_query, params)

    @staticmethod
    def get_snapshot_by_id(snapshot_id: int) -> Optional[sqlite3.Row]:
        """Fetches a single snapshot record by ID."""
        query = "SELECT * FROM billing_snapshots WHERE id = ?;"
        rows = DatabaseManager.execute_query(query, (snapshot_id,))
        return rows[0] if rows else None

    @staticmethod
    def list_snapshots(organization_id: int, payment_status: Optional[str] = None) -> List[sqlite3.Row]:
        """Lists snapshots filtered by organization_id and optional payment_status."""
        query = "SELECT * FROM billing_snapshots WHERE organization_id = ?"
        params = [organization_id]
        if payment_status:
            query += " AND payment_status = ?"
            params.append(payment_status)
        query += " ORDER BY id DESC;"
        return DatabaseManager.execute_query(query, tuple(params))

    @staticmethod
    def update_snapshot_payment_status(snapshot_id: int, payment_status: str) -> bool:
        """Updates the payment_status of a specific billing snapshot."""
        query = "UPDATE billing_snapshots SET payment_status = ? WHERE id = ?;"
        return DatabaseManager.execute_update(query, (payment_status, snapshot_id)) > 0

    @staticmethod
    def sync_daily_metrics_for_call(call_id: int) -> None:
        """
        Auto-populates daily_usage_metrics when a call completes or fails.
        Aggregates duration and call outcome counts for the call's (org, dept, user, usage_date) tuple.
        """
        call_query = "SELECT organization_id, department_id, user_id, strftime('%Y-%m-%d', created_at) as usage_date FROM calls WHERE id = ?;"
        call_rows = DatabaseManager.execute_query(call_query, (call_id,))
        if not call_rows:
            return

        call = call_rows[0]
        org_id = call["organization_id"]
        dept_id = call["department_id"]
        user_id = call["user_id"]
        usage_date = call["usage_date"]

        if user_id is not None:
            agg_query = """
                SELECT 
                    COALESCE(SUM(duration_seconds), 0.0) / 60.0 AS total_minutes,
                    SUM(CASE WHEN processing_status = 'completed' THEN 1 ELSE 0 END) AS total_calls_processed,
                    SUM(CASE WHEN processing_status = 'failed' THEN 1 ELSE 0 END) AS total_calls_failed
                FROM calls
                WHERE organization_id = ? AND department_id = ? AND user_id = ? AND strftime('%Y-%m-%d', created_at) = ?;
            """
            agg_params = (org_id, dept_id, user_id, usage_date)
            check_query = "SELECT id FROM daily_usage_metrics WHERE organization_id = ? AND department_id = ? AND user_id = ? AND usage_date = ?;"
            check_params = (org_id, dept_id, user_id, usage_date)
        else:
            agg_query = """
                SELECT 
                    COALESCE(SUM(duration_seconds), 0.0) / 60.0 AS total_minutes,
                    SUM(CASE WHEN processing_status = 'completed' THEN 1 ELSE 0 END) AS total_calls_processed,
                    SUM(CASE WHEN processing_status = 'failed' THEN 1 ELSE 0 END) AS total_calls_failed
                FROM calls
                WHERE organization_id = ? AND department_id = ? AND user_id IS NULL AND strftime('%Y-%m-%d', created_at) = ?;
            """
            agg_params = (org_id, dept_id, usage_date)
            check_query = "SELECT id FROM daily_usage_metrics WHERE organization_id = ? AND department_id = ? AND user_id IS NULL AND usage_date = ?;"
            check_params = (org_id, dept_id, usage_date)

        agg_rows = DatabaseManager.execute_query(agg_query, agg_params)
        if not agg_rows:
            return

        agg = agg_rows[0]
        total_minutes = round(float(agg["total_minutes"] or 0.0), 2)
        total_calls_processed = int(agg["total_calls_processed"] or 0)
        total_calls_failed = int(agg["total_calls_failed"] or 0)

        existing = DatabaseManager.execute_query(check_query, check_params)
        if existing:
            rec_id = existing[0]["id"]
            update_query = """
                UPDATE daily_usage_metrics
                SET total_minutes = ?, total_calls_processed = ?, total_calls_failed = ?
                WHERE id = ?;
            """
            DatabaseManager.execute_update(update_query, (total_minutes, total_calls_processed, total_calls_failed, rec_id))
        else:
            insert_query = """
                INSERT INTO daily_usage_metrics (
                    organization_id, department_id, user_id, usage_date,
                    total_minutes, total_calls_processed, total_calls_failed
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """
            DatabaseManager.execute_update(insert_query, (org_id, dept_id, user_id, usage_date, total_minutes, total_calls_processed, total_calls_failed))

    @staticmethod
    def log_monthly_snapshot(organization_id: int, tier_at_billing: str, infra_fixed_cost_charged: float,
                             per_minute_cost_charged: float, total_minutes_consumed: float,
                             total_spend_calculated: float, start_date: str, end_date: str) -> int:
        """Locks in an immutable historical billing summary record at the end of a cycle."""
        insert_query = """
            INSERT INTO billing_snapshots (
                organization_id, tier_at_billing, infra_fixed_cost_charged,
                per_minute_cost_charged, total_minutes_consumed, total_spend_calculated,
                billing_period_start, billing_period_end, payment_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unpaid');
        """
        params = (organization_id, tier_at_billing, infra_fixed_cost_charged,
                  per_minute_cost_charged, total_minutes_consumed, total_spend_calculated,
                  start_date, end_date)
        return DatabaseManager.execute_update(insert_query, params)

    @staticmethod
    def refresh_daily_metrics(organization_id: int, department_id: int, user_id: Optional[int],
                              usage_date: str, total_minutes: float, total_calls_processed: int,
                              total_calls_failed: int) -> None:
        """
        Upserts calculated daily performance metrics into the dashboard cache table.
        """
        if user_id is not None:
            check_query = "SELECT id FROM daily_usage_metrics WHERE organization_id = ? AND department_id = ? AND user_id = ? AND usage_date = ?;"
            check_params = (organization_id, department_id, user_id, usage_date)
        else:
            check_query = "SELECT id FROM daily_usage_metrics WHERE organization_id = ? AND department_id = ? AND user_id IS NULL AND usage_date = ?;"
            check_params = (organization_id, department_id, usage_date)

        existing = DatabaseManager.execute_query(check_query, check_params)
        if existing:
            rec_id = existing[0]["id"]
            update_query = """
                UPDATE daily_usage_metrics
                SET total_minutes = ?, total_calls_processed = ?, total_calls_failed = ?
                WHERE id = ?;
            """
            DatabaseManager.execute_update(update_query, (total_minutes, total_calls_processed, total_calls_failed, rec_id))
        else:
            insert_query = """
                INSERT INTO daily_usage_metrics (
                    organization_id, department_id, user_id, usage_date,
                    total_minutes, total_calls_processed, total_calls_failed
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """
            DatabaseManager.execute_update(insert_query, (organization_id, department_id, user_id, usage_date, total_minutes, total_calls_processed, total_calls_failed))

    @staticmethod
    def query_daily_usage(
        organization_id: int,
        department_id: Optional[int] = None,
        user_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[sqlite3.Row]:
        """Queries daily_usage_metrics with flexible filters."""
        query = "SELECT * FROM daily_usage_metrics WHERE organization_id = ?"
        params = [organization_id]

        if department_id is not None:
            query += " AND department_id = ?"
            params.append(department_id)
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        if start_date:
            query += " AND usage_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND usage_date <= ?"
            params.append(end_date)

        query += " ORDER BY usage_date ASC, id ASC;"
        return DatabaseManager.execute_query(query, tuple(params))