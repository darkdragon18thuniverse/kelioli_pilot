from typing import List, sqlite3
from src.app.models.base import DatabaseManager

class CallEvaluation:
    """
    Append-Only Ledger for individual call compliance rule checks.
    Intentionally omits Update and Delete methods to protect historical audit records.
    """

    @staticmethod
    def create_batch(evaluations: List[tuple]) -> None:
        """
        Inserts a batch of playbook verification outcomes simultaneously.
        Expects a list of tuples formatted as:
        (call_id, parameter_id, did_follow_rule, failure_offset_seconds, failure_reason, parameter_snapshot_text)
        """
        insert_query = """
            INSERT INTO call_evaluations (
                call_id, parameter_id, did_follow_rule, 
                failure_offset_seconds, failure_reason, parameter_snapshot_text
            ) VALUES (?, ?, ?, ?, ?, ?);
        """
        with DatabaseManager.get_connection() as conn:
            conn.executemany(insert_query, evaluations)

    @staticmethod
    def list_by_call(call_id: int) -> List[sqlite3.Row]:
        """Retrieves all playbook evaluation check rows tied to a specific call instance."""
        query = "SELECT * FROM call_evaluations WHERE call_id = ? ORDER BY id ASC;"
        return DatabaseManager.execute_query(query, (call_id,))