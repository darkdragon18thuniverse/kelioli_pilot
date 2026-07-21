from typing import Optional, List, sqlite3
from src.app.models.base import DatabaseManager

class Call:
    """
    Manages individual call transaction records.
    Enforces status state machine transitions and immutable operational log records.
    """

    VALID_STATUSES = {"pending", "transcribing", "evaluating", "completed", "failed"}

    @staticmethod
    def create(organization_id: int, department_id: int, user_id: Optional[int], 
               csv_upload_id: Optional[int], audio_url: str) -> int:
        """
        Initializes an un-processed call entry in the ingestion logging pipe.
        Defaults processing_status to 'pending'.
        """
        insert_query = """
            INSERT INTO calls (
                organization_id, department_id, user_id, csv_upload_id, 
                audio_url, processing_status
            ) VALUES (?, ?, ?, ?, ?, 'pending');
        """
        return DatabaseManager.execute_update(
            insert_query, 
            (organization_id, department_id, user_id, csv_upload_id, audio_url)
        )

    @staticmethod
    def get_by_id(call_id: int) -> Optional[sqlite3.Row]:
        """Fetch a call record complete with runtime metadata logs by primary key ID."""
        rows = DatabaseManager.execute_query("SELECT * FROM calls WHERE id = ?;", (call_id,))
        return rows[0] if rows else None

    @staticmethod
    def update_status(call_id: int, target_status: str, error_message: Optional[str] = None) -> bool:
        """
        Transitions the call through the operational state machine pipeline.
        Enforces strict schema check boundaries.
        """
        if target_status not in Call.VALID_STATUSES:
            raise ValueError(f"Invalid state transition requested: '{target_status}'")
            
        query = "UPDATE calls SET processing_status = ?, error_message = ? WHERE id = ?;"
        return DatabaseManager.execute_update(query, (target_status, error_message, call_id)) > 0

    @staticmethod
    def log_transcription(call_id: int, transcript: str, runtime_stt_model: str, 
                          duration_seconds: float, file_size_bytes: int) -> bool:
        """
        Commits verified speech text output and locks in the unalterable version signature 
        of the underlying Speech-to-Text engine used at runtime.
        """
        query = """
            UPDATE calls 
            SET transcript = ?, 
                runtime_stt_model = ?, 
                duration_seconds = ?, 
                file_size_bytes = ?,
                processing_status = 'evaluating'
            WHERE id = ?;
        """
        return DatabaseManager.execute_update(
            query, 
            (transcript, runtime_stt_model, duration_seconds, file_size_bytes, call_id)
        )

    @staticmethod
    def log_evaluation_complete(call_id: int, procedure_enquired: Optional[str], 
                                runtime_llm_model: str, prompt_tokens: int, completion_tokens: int, 
                                internal_cost: float, total_checked: int, total_passed: int) -> bool:
        """
        Finalizes tracking telemetry metrics once the OpenRouter policy engine concludes.
        Computes financial consumption metrics and freezes the active LLM version token tag.
        """
        compliance_score = 0.0
        if total_checked > 0:
            compliance_score = round((total_passed / total_checked) * 100, 2)

        query = """
            UPDATE calls 
            SET procedure_enquired = ?,
                runtime_llm_model = ?,
                upstream_tokens_prompt = ?,
                upstream_tokens_completion = ?,
                internal_execution_cost = ?,
                total_parameters_checked = ?,
                total_parameters_passed = ?,
                compliance_score_percentage = ?,
                processing_status = 'completed'
            WHERE id = ?;
        """
        params = (
            procedure_enquired, runtime_llm_model, prompt_tokens, completion_tokens,
            internal_cost, total_checked, total_passed, compliance_score, call_id
        )
        return DatabaseManager.execute_update(query, params) > 0

    @staticmethod
    def list_by_department(department_id: int) -> List[sqlite3.Row]:
        """Fetch historical calls recorded inside an isolated sandbox sector."""
        query = "SELECT * FROM calls WHERE department_id = ? ORDER BY id DESC;"
        return DatabaseManager.execute_query(query, (department_id,))