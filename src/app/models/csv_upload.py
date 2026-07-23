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
    def list_by_organization(organization_id: int, user_id: Optional[int] = None) -> List[sqlite3.Row]:
        """Retrieve historical upload batch records for an organization dashboard."""
        query = "SELECT * FROM csv_uploads WHERE organization_id = ?"
        params = [organization_id]

        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)

        query += " ORDER BY id DESC;"
        return DatabaseManager.execute_query(query, tuple(params))