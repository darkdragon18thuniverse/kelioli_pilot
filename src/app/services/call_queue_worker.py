import os
import time
from typing import Optional
from src.app.models.base import DatabaseManager
from src.app.models.call import Call
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.csv_upload import CSVUpload
from src.app.models.prepaid import Prepaid
from src.app.controllers.calls_controller import CallsController
from src.app.core.logging_config import get_logger
from src.app.core.constants import TEMP_AUDIO_DIR, prepaid_enforcement_enabled

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
            _check_and_finalize_csv_upload(csv_upload_id)
        return True

    org_dict = dict(org_row)
    dept_dict = dict(dept_row)

    # Re-check prepaid state at pickup (§2.5). A CSV enqueued while balance was
    # still positive must not be allowed to overdraft past the grace floor —
    # this per-call check plus the grace floor is what stops a large batch
    # from running past exhaustion. Blocked calls fail with a clear
    # error_message rather than crashing the queue or silently skipping.
    if prepaid_enforcement_enabled():
        grace_limit = float(org_dict.get("minute_grace_limit") or 0.0)
        infra_grace_days = int(org_dict.get("infra_grace_days") or 0)
        state_info = Prepaid.get_state(org_id, grace_limit, infra_grace_days)
        if state_info["state"] == "blocked":
            err_msg = "Insufficient prepaid balance"
            logger.warning(f"Worker: Blocking call_id={call_id} at pickup for org_id={org_id}: {err_msg} ({state_info['blocked_reason']})")
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
                _check_and_finalize_csv_upload(csv_upload_id)
            return True

    resolved_path = None
    is_temp = False

    try:
        resolved_path, is_temp = CallsController._resolve_audio_source(call["audio_url"])
        result = CallsController._run_evaluation_pipeline(call_id, org_dict, dept_dict, resolved_path)
        logger.info(f"Worker successfully completed evaluation for call_id={call_id}")
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

    return True


def _check_and_finalize_csv_upload(csv_upload_id: int) -> None:
    CSVUpload.sync_upload_stats(csv_upload_id)



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
