import sqlite3
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from src.app.models.base import DatabaseManager


class Call:
    @staticmethod
    def create(organization_id: int, department_id: int, audio_url: str,
               user_id: Optional[int] = None, csv_upload_id: Optional[int] = None,
               duration_seconds: float = 0.0, file_size_bytes: int = 0,
               procedure_enquired: Optional[str] = None) -> int:
        query = """
            INSERT INTO calls (
                organization_id, department_id, user_id, csv_upload_id,
                audio_url, duration_seconds, file_size_bytes, procedure_enquired,
                processing_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending');
        """
        return DatabaseManager.execute_update(
            query,
            (organization_id, department_id, user_id, csv_upload_id, audio_url, duration_seconds, file_size_bytes, procedure_enquired)
        )

    @staticmethod
    def get_by_id(call_id: int) -> Optional[sqlite3.Row]:
        query = "SELECT * FROM calls WHERE id = ?;"
        rows = DatabaseManager.execute_query(query, (call_id,))
        return rows[0] if rows else None

    @staticmethod
    def update_evaluation_results(call_id: int, transcript: str, total_checked: int,
                                  total_passed: int, compliance_score_percentage: float,
                                  processing_status: str = "completed",
                                  error_message: Optional[str] = None) -> bool:
        query = """
            UPDATE calls SET
                transcript = ?,
                total_parameters_checked = ?,
                total_parameters_passed = ?,
                compliance_score_percentage = ?,
                processing_status = ?,
                error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
        """
        updated = DatabaseManager.execute_update(
            query, (transcript, total_checked, total_passed, compliance_score_percentage, processing_status, error_message, call_id)
        ) > 0
        if updated:
            from src.app.models.billing import Billing
            Billing.sync_daily_metrics_for_call(call_id)
        return updated

    @staticmethod
    def list_calls(organization_id: int, department_id: Optional[int] = None,
                   user_id: Optional[int] = None, status_filter: Optional[str] = None) -> List[sqlite3.Row]:
        query = "SELECT * FROM calls WHERE organization_id = ?"
        params = [organization_id]

        if department_id:
            query += " AND department_id = ?"
            params.append(department_id)
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if status_filter:
            query += " AND processing_status = ?"
            params.append(status_filter)

        query += " ORDER BY id DESC;"
        return DatabaseManager.execute_query(query, tuple(params))

    @staticmethod
    def get_monthly_duration_seconds(organization_id: int, year: Optional[int] = None, month: Optional[int] = None) -> float:
        """
        Sums duration_seconds for all calls belonging to organization_id in the given
        (or current, if omitted) calendar month. Used to enforce max_monthly_minutes caps.
        """
        now = datetime.now(timezone.utc)
        target_year = year or now.year
        target_month = month or now.month
        month_str = f"{target_year:04d}-{target_month:02d}"

        query = """
            SELECT COALESCE(SUM(duration_seconds), 0.0) as total_seconds
            FROM calls
            WHERE organization_id = ? AND strftime('%Y-%m', created_at) = ?;
        """
        rows = DatabaseManager.execute_query(query, (organization_id, month_str))
        return float(rows[0]["total_seconds"]) if rows else 0.0


class CallEvaluation:
    @staticmethod
    def create_batch(evaluations: List[Dict[str, Any]]) -> bool:
        if not evaluations:
            return True

        query = """
            INSERT INTO call_evaluations (
                call_id, parameter_id, did_follow_rule, failure_offset_seconds, failure_reason, parameter_snapshot_text
            ) VALUES (?, ?, ?, ?, ?, ?);
        """
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            for ev in evaluations:
                cursor.execute(query, (
                    ev["call_id"], ev["parameter_id"], ev["did_follow_rule"],
                    ev.get("failure_offset_seconds"), ev.get("failure_reason"), ev.get("parameter_snapshot_text")
                ))
            conn.commit()
        return True

    @staticmethod
    def list_by_call_id(call_id: int) -> List[sqlite3.Row]:
        query = """
            SELECT ce.*, cp.parameter_name, cp.severity_level
            FROM call_evaluations ce
            JOIN compliance_parameters cp ON ce.parameter_id = cp.id
            WHERE ce.call_id = ?;
        """
        return DatabaseManager.execute_query(query, (call_id,))
