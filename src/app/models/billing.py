from typing import List, Optional, sqlite3
from src.app.models.base import DatabaseManager

class Billing:
    """
    Handles immutable monthly accounting closures and time-series performance aggregations.
    """

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
        Uses standard conflict replacement to eliminate processing lag.
        """
        upsert_query = """
            INSERT INTO daily_usage_metrics (
                organization_id, department_id, user_id, usage_date,
                total_minutes, total_calls_processed, total_calls_failed
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(organization_id, department_id, user_id, usage_date) DO UPDATE SET
                total_minutes = EXCLUDED.total_minutes,
                total_calls_processed = EXCLUDED.total_calls_processed,
                total_calls_failed = EXCLUDED.total_calls_failed;
        """
        params = (organization_id, department_id, user_id, usage_date,
                  total_minutes, total_calls_processed, total_calls_failed)
        DatabaseManager.execute_update(upsert_query, params)

    @staticmethod
    def get_dashboard_metrics(organization_id: int, start_date: str, end_date: str) -> List[sqlite3.Row]:
        """Fetches pre-compiled dashboard numbers, bypassing heavy multi-table analytical query computation."""
        query = """
            SELECT usage_date, sum(total_minutes) as minutes, sum(total_calls_processed) as processed,
                   sum(total_calls_failed) as failed
            FROM daily_usage_metrics
            WHERE organization_id = ? AND usage_date BETWEEN ? AND ?
            GROUP BY usage_date ORDER BY usage_date ASC;
        """
        return DatabaseManager.execute_query(query, (organization_id, start_date, end_date))