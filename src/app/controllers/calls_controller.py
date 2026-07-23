import os
import shutil
import uuid
import hashlib
import httpx
from typing import Dict, Any, List, Optional, Tuple
from fastapi import HTTPException, status, UploadFile
from src.app.models.compliance import ComplianceParameter
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.call import Call, CallEvaluation
from src.app.models.csv_upload import CSVUpload
from src.app.services.stt import STTService, LLMService
from src.app.core.logging_config import get_logger

logger = get_logger(__name__)

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None

TEMP_AUDIO_DIR = "./media/temp_audio"
ROLES = {"superadmin": 1, "admin": 2, "manager": 3, "agent": 4}


class CallsController:
    @staticmethod
    def _verify_role(current_user: Dict[str, Any], allowed_role_ids: List[int]) -> None:
        if current_user["role_id"] not in allowed_role_ids:
            logger.warning(f"RBAC Denied in Calls: User {current_user['id']} (role_id: {current_user['role_id']}) tried operation requiring: {allowed_role_ids}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Insufficient administrative privileges."
            )

    @staticmethod
    def _enforce_org_active(org: Dict[str, Any]) -> None:
        """Blocks call processing for any organization not in 'active' status
        (covers both manually-suspended orgs and orgs auto-flagged 'limit_exceeded')."""
        if org["status"] != "active":
            logger.warning(f"Call processing blocked: Organization org_id={org['id']} status is '{org['status']}'")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Organization is not active (status: '{org['status']}'). Call processing is blocked."
            )

    @staticmethod
    def _resolve_audio_source(audio_url: str) -> Tuple[str, bool]:
        """
        Resolves an audio_url into a usable local file path.
        Supports http(s) URLs (downloaded to a temp file) and already-local paths (passed through).
        Returns (local_path, is_temp_download) so callers know whether to clean up afterwards.
        """
        if audio_url.startswith("http://") or audio_url.startswith("https://"):
            os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
            file_ext = os.path.splitext(audio_url.split("?")[0])[1] or ".wav"
            temp_filename = f"{uuid.uuid4()}{file_ext}"
            local_path = os.path.join(TEMP_AUDIO_DIR, temp_filename)
            try:
                logger.info(f"Downloading remote audio source from '{audio_url}' to '{local_path}'")
                with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                    resp = client.get(audio_url)
                    resp.raise_for_status()
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
            except Exception as e:
                logger.error(f"Failed downloading remote audio_url '{audio_url}': {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to download audio_url '{audio_url}': {str(e)}"
                )
            return local_path, True
        return audio_url, False

    @staticmethod
    def _get_audio_duration_seconds(local_path: str) -> float:
        """Reads duration metadata via mutagen. Fails soft (returns 0.0) if unreadable or mutagen missing."""
        if MutagenFile is None:
            return 0.0
        try:
            audio = MutagenFile(local_path)
            if audio is not None and audio.info is not None:
                duration = float(audio.info.length)
                logger.debug(f"Audio file '{local_path}' duration read: {duration:.2f}s")
                return duration
        except Exception as e:
            logger.debug(f"Could not read audio duration for '{local_path}': {e}")
        return 0.0

    @staticmethod
    def _check_and_apply_monthly_cap(organization_id: int, max_monthly_minutes: Optional[float]) -> None:
        """After a call completes, sums this month's usage and flips the org to 'limit_exceeded'
        if it has crossed max_monthly_minutes. Superadmin resets status back to 'active' manually
        via the existing update-organization endpoint."""
        if max_monthly_minutes is None:
            return
        total_seconds = Call.get_monthly_duration_seconds(organization_id)
        total_minutes = total_seconds / 60.0
        if total_minutes > max_monthly_minutes:
            logger.warning(f"Organization org_id={organization_id} exceeded monthly limit: consumed {total_minutes:.2f}m / cap {max_monthly_minutes:.2f}m. Updating status to 'limit_exceeded'")
            Organization.update(organization_id, {"status": "limit_exceeded"})

    @staticmethod
    def _run_evaluation_pipeline(call_id: int, org: Any, dept: Any, audio_path: str) -> Dict[str, Any]:
        """Shared STT + LLM evaluation pipeline used by both single-upload and CSV batch flows."""
        logger.info(f"Pipeline Execution: Starting STT for call_id={call_id}")
        stt_result = STTService.transcribe(audio_path)
        transcript = stt_result.get("transcript", "")
        logger.info(f"Pipeline Execution: STT completed for call_id={call_id}. Transcript length: {len(transcript)} chars")

        raw_params = ComplianceParameter.list_by_department(org["id"], dept["id"])
        active_params = [dict(p) for p in raw_params if p["is_active"] == 1] if raw_params else []
        logger.info(f"Pipeline Execution: Running LLM evaluation against {len(active_params)} active compliance parameters for call_id={call_id}")

        # Only send the fields the LLM actually needs to evaluate + echo back parameter_id.
        # Avoids leaking organization_id/department_id/is_active/created_at into the prompt.
        llm_params = [
            {
                "id": p["id"],
                "parameter_name": p["parameter_name"],
                "rule_description": p["rule_description"],
                "severity_level": p["severity_level"]
            }
            for p in active_params
        ]

        llm_model = org["llm_model_routing"] or "openrouter/free"
        evaluation_result = LLMService.evaluate(
            model=llm_model,
            company_context=org["company_context"],
            department_context=dept["department_context"],
            parameters=llm_params,
            transcript=transcript
        )
        procedure_enquired = evaluation_result.get("procedure_enquired", "General Inquiry")
        eval_items = evaluation_result.get("evaluations", [])
        passed_count = sum(1 for item in eval_items if item.get("did_follow_rule") == 1)
        total_checked = len(eval_items)
        score = (passed_count / total_checked * 100.0) if total_checked > 0 else 100.0

        logger.info(f"Pipeline Execution: LLM evaluation done for call_id={call_id}. Score: {score:.1f}% ({passed_count}/{total_checked} passed)")

        evaluations_to_save = []
        for item in eval_items:
            param_match = next((p for p in active_params if p["id"] == item["parameter_id"]), None)
            snapshot_text = param_match["rule_description"] if param_match else ""
            evaluations_to_save.append({
                "call_id": call_id,
                "parameter_id": item["parameter_id"],
                "did_follow_rule": item["did_follow_rule"],
                "failure_offset_seconds": None,  # STT provides transcript only, no timestamps to base this on
                "failure_reason": item.get("failure_reason"),
                "parameter_snapshot_text": snapshot_text
            })
        if evaluations_to_save:
            CallEvaluation.create_batch(evaluations_to_save)
        Call.update_evaluation_results(
            call_id=call_id,
            transcript=transcript,
            total_checked=total_checked,
            total_passed=passed_count,
            compliance_score_percentage=score,
            processing_status="completed"
        )
        return {
            "procedure_enquired": procedure_enquired,
            "compliance_score_percentage": score
        }

    @staticmethod
    def process_audio_upload(current_user: Dict[str, Any], file: UploadFile,
                             organization_id: int, department_id: int,
                             user_id: Optional[int] = None) -> Dict[str, Any]:
        """Saves file locally, retains through retries, cleans up temp file on LLM success."""
        CallsController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])
        if current_user["role_id"] == ROLES["admin"] and current_user["organization_id"] != organization_id:
            logger.warning(f"Cross-tenant call processing denied for user_id={current_user['id']} targeting org_id={organization_id}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot process calls outside your organization.")
        if current_user["role_id"] == ROLES["manager"]:
            if current_user["organization_id"] != organization_id or current_user["department_id"] != department_id:
                logger.warning(f"Cross-department call processing denied for manager user_id={current_user['id']}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Managers can only process calls for their assigned department.")
        org = Organization.get_by_id(organization_id)
        dept = Department.get_by_id(department_id)
        if not org or not dept or dept["organization_id"] != organization_id:
            logger.warning(f"Invalid org/dept context for call upload: org_id={organization_id}, dept_id={department_id}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid organization or department context.")
        org_dict = dict(org)
        CallsController._enforce_org_active(org_dict)

        os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
        file_ext = os.path.splitext(file.filename)[1] or ".wav"
        temp_filename = f"{uuid.uuid4()}{file_ext}"
        local_file_path = os.path.join(TEMP_AUDIO_DIR, temp_filename)
        with open(local_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        file_size = os.path.getsize(local_file_path)
        duration_seconds = CallsController._get_audio_duration_seconds(local_file_path)

        call_id = Call.create(
            organization_id=organization_id,
            department_id=department_id,
            user_id=user_id or current_user["id"],
            audio_url=local_file_path,
            duration_seconds=duration_seconds,
            file_size_bytes=file_size
        )
        logger.info(f"Created call record call_id={call_id} for file '{file.filename}' ({file_size} bytes, {duration_seconds:.1f}s)")

        try:
            result = CallsController._run_evaluation_pipeline(call_id, org_dict, dict(dept), local_file_path)
            if os.path.exists(local_file_path):
                os.remove(local_file_path)
            CallsController._check_and_apply_monthly_cap(organization_id, org_dict.get("max_monthly_minutes"))
            logger.info(f"Call call_id={call_id} evaluated successfully. Status: completed")
            return {
                "status": "success",
                "call_id": call_id,
                "procedure_enquired": result["procedure_enquired"],
                "compliance_score_percentage": result["compliance_score_percentage"],
                "message": "Call evaluated successfully."
            }
        except Exception as e:
            logger.exception(f"Call processing pipeline failed for call_id={call_id}: {e}")
            Call.update_evaluation_results(
                call_id=call_id,
                transcript="",
                total_checked=0,
                total_passed=0,
                compliance_score_percentage=0.0,
                processing_status="failed",
                error_message=str(e)
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Call processing pipeline failed: {str(e)}"
            )

    @staticmethod
    def list_calls(current_user: Dict[str, Any], organization_id: Optional[int] = None,
                    department_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Lists calls with RBAC tenant/department/self scoping.
        """
        CallsController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])
        effective_org_id = organization_id
        effective_dept_id = department_id
        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"], ROLES["agent"]]:
            effective_org_id = current_user["organization_id"]
        if current_user["role_id"] in [ROLES["manager"], ROLES["agent"]]:
            effective_dept_id = current_user["department_id"]
        if current_user["role_id"] == ROLES["superadmin"] and effective_org_id is None:
            logger.warning("Superadmin call query missing organization_id filter.")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="organization_id is required when listing calls as Superadmin."
            )
        rows = Call.list_calls(
            organization_id=effective_org_id,
            department_id=effective_dept_id,
            user_id=current_user["id"] if current_user["role_id"] == ROLES["agent"] else None
        )
        calls = [dict(r) for r in rows] if rows else []
        for c in calls:
            c["evaluations"] = []
        logger.info(f"Retrieved {len(calls)} call records for user_id={current_user['id']} (effective org={effective_org_id}, dept={effective_dept_id})")
        return {"calls": calls}

    @staticmethod
    def get_call_details(current_user: Dict[str, Any], call_id: int) -> Dict[str, Any]:
        CallsController._verify_role(current_user, [1, 2, 3, 4])
        call = Call.get_by_id(call_id)
        if not call:
            logger.warning(f"Call record not found: call_id={call_id}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call record not found.")
        call_dict = dict(call)
        if current_user["role_id"] in [2, 3, 4] and call_dict["organization_id"] != current_user["organization_id"]:
            logger.warning(f"Cross-tenant call view denied for call_id={call_id} to user_id={current_user['id']}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
        if current_user["role_id"] in [3, 4] and call_dict["department_id"] != current_user["department_id"]:
            logger.warning(f"Cross-department call view denied for call_id={call_id} to user_id={current_user['id']}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
        if current_user["role_id"] == 4 and call_dict["user_id"] != current_user["id"]:
            logger.warning(f"Cross-agent call view denied for call_id={call_id} to agent user_id={current_user['id']}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
        eval_rows = CallEvaluation.list_by_call_id(call_id)
        call_dict["evaluations"] = [dict(r) for r in eval_rows] if eval_rows else []
        logger.info(f"Retrieved call details for call_id={call_id} with {len(call_dict['evaluations'])} evaluations")
        return call_dict

    @staticmethod
    def process_audio_csv(current_user: Dict[str, Any], file: UploadFile) -> Dict[str, Any]:
        """
        Parses a batch CSV file, tracks progress via the CSVUpload model.
        """
        CallsController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"]])
        import csv
        import io
        raw_bytes = file.file.read()
        content = raw_bytes.decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        total_records = len(rows)
        if total_records == 0:
            logger.warning(f"CSV processing aborted: file '{file.filename}' is empty or missing headers.")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV file is empty or missing headers.")
        file_hash = hashlib.sha256(raw_bytes).hexdigest()
        
        batch_organization_id = current_user.get("organization_id")
        if batch_organization_id is None:
            first_row_org = rows[0].get("organization_id")
            if not first_row_org:
                logger.warning("Superadmin CSV upload missing organization_id in CSV header/rows.")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="organization_id column is required in the CSV when uploading as Superadmin."
                )
            batch_organization_id = int(first_row_org)
        try:
            csv_upload_id = CSVUpload.create(
                organization_id=batch_organization_id,
                user_id=current_user["id"],
                filename=file.filename,
                file_hash=file_hash,
                total_records=total_records
            )
            logger.info(f"Initiated CSV batch upload processing: upload_id={csv_upload_id}, filename='{file.filename}', total_records={total_records}, hash={file_hash[:8]}")
        except ValueError as e:
            logger.warning(f"Duplicate CSV batch upload detected for file '{file.filename}': {e}")
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
        processed_count = 0
        failed_count = 0
        for idx, row in enumerate(rows, 1):
            resolved_path = None
            is_temp = False
            call_id = None
            try:
                row_org_id_raw = row.get("organization_id")
                row_dept_id_raw = row.get("department_id")
                row_user_id_raw = row.get("user_id")
                if not row_org_id_raw or not row_dept_id_raw:
                    logger.warning(f"CSV Row #{idx} skipped: missing org_id or dept_id")
                    failed_count += 1
                    CSVUpload.increment_progress(csv_upload_id, is_success=False)
                    continue
                org_id = int(row_org_id_raw)
                dept_id = int(row_dept_id_raw)
                u_id = int(row_user_id_raw) if row_user_id_raw else current_user["id"]
                audio_path = row.get("audio_url")
                if not audio_path:
                    logger.warning(f"CSV Row #{idx} skipped: missing audio_url")
                    failed_count += 1
                    CSVUpload.increment_progress(csv_upload_id, is_success=False)
                    continue
                if current_user["role_id"] == ROLES["admin"] and current_user["organization_id"] != org_id:
                    logger.warning(f"CSV Row #{idx} skipped: org_id {org_id} mismatch for Tenant Admin")
                    failed_count += 1
                    CSVUpload.increment_progress(csv_upload_id, is_success=False)
                    continue
                if current_user["role_id"] == ROLES["manager"] and (current_user["organization_id"] != org_id or current_user["department_id"] != dept_id):
                    logger.warning(f"CSV Row #{idx} skipped: dept_id {dept_id} mismatch for Manager")
                    failed_count += 1
                    CSVUpload.increment_progress(csv_upload_id, is_success=False)
                    continue
                org = Organization.get_by_id(org_id)
                dept = Department.get_by_id(dept_id)
                if not org or not dept or dept["organization_id"] != org_id:
                    logger.warning(f"CSV Row #{idx} skipped: invalid org/dept ID in DB")
                    failed_count += 1
                    CSVUpload.increment_progress(csv_upload_id, is_success=False)
                    continue
                org_dict = dict(org)
                if org_dict["status"] != "active":
                    logger.warning(f"CSV Row #{idx} skipped: organization status is '{org_dict['status']}'")
                    failed_count += 1
                    CSVUpload.increment_progress(csv_upload_id, is_success=False)
                    continue
                resolved_path, is_temp = CallsController._resolve_audio_source(audio_path)
                duration_seconds = CallsController._get_audio_duration_seconds(resolved_path)
                file_size = os.path.getsize(resolved_path) if os.path.exists(resolved_path) else 0
                call_id = Call.create(
                    organization_id=org_id,
                    department_id=dept_id,
                    user_id=u_id,
                    csv_upload_id=csv_upload_id,
                    audio_url=audio_path,
                    duration_seconds=duration_seconds,
                    file_size_bytes=file_size
                )
                CallsController._run_evaluation_pipeline(call_id, org_dict, dict(dept), resolved_path)
                processed_count += 1
                CSVUpload.increment_progress(csv_upload_id, is_success=True)
                CallsController._check_and_apply_monthly_cap(org_id, org_dict.get("max_monthly_minutes"))
            except Exception as e:
                logger.error(f"CSV Row #{idx} call pipeline error: {e}")
                if call_id is not None:
                    Call.update_evaluation_results(
                        call_id=call_id,
                        transcript="",
                        total_checked=0,
                        total_passed=0,
                        compliance_score_percentage=0.0,
                        processing_status="failed",
                        error_message=str(e)
                    )
                failed_count += 1
                CSVUpload.increment_progress(csv_upload_id, is_success=False)
            finally:
                if is_temp and resolved_path and os.path.exists(resolved_path):
                    os.remove(resolved_path)
        final_status = "completed" if failed_count == 0 else "failed" if processed_count == 0 else "completed"
        CSVUpload.update_status(csv_upload_id, final_status)
        logger.info(f"CSV batch processing upload_id={csv_upload_id} finalized: status='{final_status}', processed={processed_count}, failed={failed_count}")
        return {
            "status": "success",
            "csv_upload_id": csv_upload_id,
            "total_records": total_records,
            "processed_records": processed_count,
            "failed_records": failed_count,
            "batch_status": final_status,
            "message": "CSV batch processing completed."
        }

    @staticmethod
    def list_csv_uploads(
        current_user: Dict[str, Any],
        organization_id: int
    ) -> Dict[str, Any]:
        CallsController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])

        # Tenant scoping
        if current_user["role_id"] != ROLES["superadmin"]:
            if organization_id != current_user["organization_id"]:
                logger.warning(f"Cross-tenant CSV upload list access denied for user_id={current_user['id']} requesting org_id={organization_id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Cannot access CSV uploads outside your organization."
                )

        org = Organization.get_by_id(organization_id)
        if not org:
            logger.warning(f"CSV upload list query failed: Organization org_id={organization_id} not found.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization record not found."
            )

        rows = CSVUpload.list_by_organization(organization_id)
        csv_uploads = [dict(row) for row in rows] if rows else []
        logger.info(f"Retrieved {len(csv_uploads)} CSV upload records for org_id={organization_id}")
        return {"csv_uploads": csv_uploads}

    @staticmethod
    def get_csv_upload_details(
        current_user: Dict[str, Any],
        upload_id: int
    ) -> Dict[str, Any]:
        CallsController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])

        upload = CSVUpload.get_by_id(upload_id)
        if not upload:
            logger.warning(f"CSV upload record not found: upload_id={upload_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="CSV upload record not found."
            )

        upload_dict = dict(upload)

        # Tenant scoping
        if current_user["role_id"] != ROLES["superadmin"]:
            if upload_dict["organization_id"] != current_user["organization_id"]:
                logger.warning(f"Cross-tenant CSV upload view denied for upload_id={upload_id} to user_id={current_user['id']}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Cannot view CSV upload from another organization."
                )

        logger.info(f"Retrieved CSV upload details for upload_id={upload_id}")
        return upload_dict

