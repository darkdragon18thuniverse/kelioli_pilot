import os
import time
from typing import Optional
from src.app.models.base import DatabaseManager
from src.app.models.call import Call
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.csv_upload import CSVUpload
from src.app.controllers.calls_controller import CallsController
from src.app.core.logging_config import get_logger
from src.app.core.constants import TEMP_AUDIO_DIR

logger = get_logger(__name__)

_stop_event = False

def process_next_pending_call() -> bool:
    """
    Polls for a single pending call, claims it, executes the pipeline,
    updates telemetry / CSV progress, and enforces monthly caps.
    Returns True if a call was processed, False if queue was empty.
    """
    find_query = "SELECT id FROM calls WHERE processing_status = 'pending' ORDER BY id ASC LIMIT 1;"
    rows = DatabaseManager.execute_query(find_query)
    if not rows:
        return False

    call_id = rows[0]["id"]
    claim_query = "UPDATE calls SET processing_status = 'transcribing' WHERE id = ? AND processing_status = 'pending';"
    claimed = DatabaseManager.execute_update(claim_query, (call_id,)) > 0
    if not claimed:
        return False

    logger.info(f"Worker claimed call_id={call_id} for evaluation.")
    call_row = Call.get_by_id(call_id)
    if not call_row:
        logger.error(f"Worker: Call call_id={call_id} not found after claim.")
        return True

    call = dict(call_row)
    csv_upload_id = call.get("csv_upload_id")
    org_id = call["organization_id"]
    dept_id = call["department_id"]

    org_row = Organization.get_by_id(org_id)
    dept_row = Department.get_by_id(dept_id)

    if not org_row or not dept_row or org_row["status"] != "active":
        err_msg = f"Organization (status={org_row['status'] if org_row else 'None'}) or Department context invalid."
        logger.warning(f"Worker: Skipping call_id={call_id}: {err_msg}")
        Call.update_evaluation_results(
            call_id=call_id,
            transcript="",
            total_checked=0,
            total_passed=0,
            compliance_score_percentage=None,
            processing_status="failed",
            error_message=err_msg
        )
        if call.get("audio_url") and os.path.exists(call["audio_url"]) and TEMP_AUDIO_DIR in call["audio_url"]:
            try:
                os.remove(call["audio_url"])
            except Exception as cleanup_err:
                logger.warning(f"Worker failed cleaning single-upload temp file '{call['audio_url']}': {cleanup_err}")
        if csv_upload_id:
            CSVUpload.increment_progress(csv_upload_id, is_success=False)
            _check_and_finalize_csv_upload(csv_upload_id)
        if org_row:
            CallsController._check_and_apply_monthly_cap(org_id, org_row["max_monthly_minutes"])
        return True

    org_dict = dict(org_row)
    dept_dict = dict(dept_row)
    resolved_path = None
    is_temp = False

    try:
        resolved_path, is_temp = CallsController._resolve_audio_source(call["audio_url"])
        result = CallsController._run_evaluation_pipeline(call_id, org_dict, dept_dict, resolved_path)
        logger.info(f"Worker successfully completed evaluation for call_id={call_id}")
        if csv_upload_id:
            CSVUpload.increment_progress(csv_upload_id, is_success=True)
    except Exception as e:
        logger.exception(f"Worker: Pipeline execution failed for call_id={call_id}: {e}")
        Call.update_evaluation_results(
            call_id=call_id,
            transcript="",
            total_checked=0,
            total_passed=0,
            compliance_score_percentage=None,
            processing_status="failed",
            error_message=str(e)
        )
        if csv_upload_id:
            CSVUpload.increment_progress(csv_upload_id, is_success=False)
    finally:
        # Clean up temporary downloaded remote file or local uploaded file in temp_audio
        if is_temp and resolved_path and os.path.exists(resolved_path):
            try:
                os.remove(resolved_path)
            except Exception as cleanup_err:
                logger.warning(f"Worker failed cleaning temp audio file '{resolved_path}': {cleanup_err}")
        elif call.get("audio_url") and os.path.exists(call["audio_url"]) and TEMP_AUDIO_DIR in call["audio_url"]:
            try:
                os.remove(call["audio_url"])
            except Exception as cleanup_err:
                logger.warning(f"Worker failed cleaning single-upload temp file '{call['audio_url']}': {cleanup_err}")

        if csv_upload_id:
            _check_and_finalize_csv_upload(csv_upload_id)

        # Runs for ALL calls (single-file uploads and CSV-batch rows) after each call completes
        CallsController._check_and_apply_monthly_cap(org_id, org_dict.get("max_monthly_minutes"))

    return True


def _check_and_finalize_csv_upload(csv_upload_id: int) -> None:
    upload_row = CSVUpload.get_by_id(csv_upload_id)
    if not upload_row:
        return
    upload = dict(upload_row)
    processed = upload["processed_records"]
    failed = upload["failed_records"]
    total = upload["total_records"]

    if (processed + failed) >= total:
        final_status = "completed" if failed == 0 else ("failed" if processed == 0 else "completed")
        CSVUpload.update_status(csv_upload_id, final_status)
        logger.info(f"Worker: Finalized CSV upload upload_id={csv_upload_id} as '{final_status}' (processed={processed}, failed={failed}, total={total})")


def run_worker() -> None:
    """
    Main loop for background daemon thread.
    Continuously polls for pending calls with a 2-second sleep when idle.
    """
    logger.info("Call Queue Worker started in background daemon thread.")
    while not _stop_event:
        try:
            processed = process_next_pending_call()
            if not processed:
                time.sleep(2.0)
        except Exception as e:
            logger.exception(f"Unexpected error in call queue worker loop: {e}")
            time.sleep(2.0)
