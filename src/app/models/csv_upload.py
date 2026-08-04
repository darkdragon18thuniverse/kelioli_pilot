import sqlite3
from typing import Optional, List
from src.app.models.base import DatabaseManager

class CSVUpload:
    """
    Manages batch audio ingestion logs.
    Enforces file deduplication via SHA-256 hashing and provides atomic telemetry updates.
    """

    @staticmethod
    def create(organization_id: int, user_id: Optional[int], filename: str, file_hash: str, total_records: int = 0) -> int:
        """
        Logs a batch ingestion job.
        Checks if a file with the identical hash hash has already been processed or is processing 
        to prevent duplicate execution.
        """
        hash_clean = file_hash.strip().lower()

        # Deduplication check: verify if this file payload has already passed our ingestion layer
        query_check = "SELECT id, status FROM csv_uploads WHERE organization_id = ? AND file_hash = ?;"
        existing = DatabaseManager.execute_query(query_check, (organization_id, hash_clean))
        
        if existing:
            status = existing[0]["status"]
            if status in ("processing", "completed"):
                raise ValueError(f"Deduplication Block: This file hash ({hash_clean}) has already been uploaded and is status: '{status}'.")

        insert_query = """
            INSERT INTO csv_uploads (organization_id, user_id, filename, file_hash, total_records, processed_records, failed_records, status)
            VALUES (?, ?, ?, ?, ?, 0, 0, 'processing');
        """
        return DatabaseManager.execute_update(
            insert_query, 
            (organization_id, user_id, filename, hash_clean, total_records)
        )

    @staticmethod
    def get_by_id(upload_id: int) -> Optional[sqlite3.Row]:
        """Fetch upload execution record details using the primary key ID."""
        rows = DatabaseManager.execute_query("SELECT * FROM csv_uploads WHERE id = ?;", (upload_id,))
        return rows[0] if rows else None

    @staticmethod
    def increment_progress(upload_id: int, is_success: bool) -> bool:
        """
        Atomically increments telemetry progress variables.
        Utilizes SQL mathematical expressions to eliminate Python variable overwrite race conditions.
        """
        if is_success:
            query = "UPDATE csv_uploads SET processed_records = processed_records + 1 WHERE id = ?;"
        else:
            query = "UPDATE csv_uploads SET failed_records = failed_records + 1 WHERE id = ?;"
            
        return DatabaseManager.execute_update(query, (upload_id,)) > 0

    @staticmethod
    def update_status(upload_id: int, target_status: str) -> bool:
        """Updates the status of the batch job ('completed', 'failed')."""
        if target_status not in ("processing", "completed", "failed"):
            raise ValueError(f"Invalid batch state assignment: '{target_status}'")
            
        query = "UPDATE csv_uploads SET status = ? WHERE id = ?;"
        return DatabaseManager.execute_update(query, (target_status, upload_id)) > 0

    @staticmethod
    def sync_upload_stats(upload_id: int) -> bool:
        """
        Recalculates processed_records, failed_records, and status for a csv_upload
        based on the current processing status of all calls associated with that upload_id.
        """
        if not upload_id:
            return False

        upload_row = CSVUpload.get_by_id(upload_id)
        if not upload_row:
            return False

        upload = dict(upload_row)
        total_records = upload["total_records"]

        query = """
            SELECT 
                COUNT(*) as total_calls,
                SUM(CASE WHEN processing_status = 'completed' THEN 1 ELSE 0 END) as completed_calls,
                SUM(CASE WHEN processing_status = 'failed' THEN 1 ELSE 0 END) as failed_calls,
                SUM(CASE WHEN processing_status IN ('pending', 'transcribing', 'evaluating') THEN 1 ELSE 0 END) as in_progress_calls
            FROM calls
            WHERE csv_upload_id = ?;
        """
        rows = DatabaseManager.execute_query(query, (upload_id,))
        if not rows:
            return False

        r = rows[0]
        completed = int(r["completed_calls"] or 0)
        failed = int(r["failed_calls"] or 0)
        in_progress = int(r["in_progress_calls"] or 0)
        total_calls = int(r["total_calls"] or 0)

        # Un-inserted validation failures: total_records - total_calls
        uninserted_failures = max(0, total_records - total_calls)
        total_failed = failed + uninserted_failures

        if in_progress > 0:
            final_status = "processing"
        elif total_failed == 0 or completed > 0:
            final_status = "completed"
        else:
            final_status = "failed"

        update_query = """
            UPDATE csv_uploads
            SET processed_records = ?, failed_records = ?, status = ?
            WHERE id = ?;
        """
        return DatabaseManager.execute_update(update_query, (completed, total_failed, final_status, upload_id)) > 0


    @staticmethod
    def list_by_organization(organization_id: int, user_id: Optional[int] = None) -> List[sqlite3.Row]:
        """Retrieve historical upload batch records for an organization dashboard."""
        query = "SELECT * FROM csv_uploads WHERE organization_id = ?"
        params = [organization_id]

        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)

        query += " ORDER BY id DESC;"
        return DatabaseManager.execute_query(query, tuple(params))