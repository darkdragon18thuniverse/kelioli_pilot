import os
import shutil
import uuid
import hashlib
import json
import httpx
from typing import Dict, Any, List, Optional, Tuple
from fastapi import HTTPException, status, UploadFile
from src.app.models.compliance import ComplianceParameter
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.call import Call, CallEvaluation
from src.app.models.csv_upload import CSVUpload
from src.app.models.prepaid import Prepaid
from src.app.services.stt import STTService, LLMService
from src.app.core.logging_config import get_logger
from src.app.core.roles import ROLES
from src.app.core.constants import TEMP_AUDIO_DIR, MINIMUM_BILLABLE_MINUTES, prepaid_enforcement_enabled

logger = get_logger(__name__)

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None



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
    def _enforce_prepaid_balance(org: Dict[str, Any]) -> None:
        """Blocks call intake with 402 when the org's prepaid state is 'blocked'
        (§2.4/§2.5). No-ops entirely when PREPAID_ENFORCEMENT_ENABLED=false,
        which buys the cutover window between deploy and recording opening
        recharges (see PREPAID_BILLING_PLAN.md §5.3)."""
        if not prepaid_enforcement_enabled():
            return
        grace_limit = float(org.get("minute_grace_limit") or 0.0)
        infra_grace_days = int(org.get("infra_grace_days") or 0)
        state_info = Prepaid.get_state(org["id"], grace_limit, infra_grace_days)
        if state_info["state"] == "blocked":
            reason = state_info["blocked_reason"] or "Prepaid balance exhausted."
            logger.warning(f"Call processing blocked: Organization org_id={org['id']} prepaid state is 'blocked' ({reason})")
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Insufficient prepaid balance: {reason}"
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
    def _get_audio_duration_seconds(local_path: str, call_id: Optional[int] = None, audio_url: Optional[str] = None) -> float:
        """
        Resolves audio duration for billing purposes (D4/§2.8). Under prepaid,
        duration_seconds IS the invoice, so this must not silently return 0.0.

        Resolution order: PyAV primary (same container/demux entry point stt.py
        already uses for >30s chunking — no new dependency), mutagen fallback.
        If both fail, returns 0.0 and the caller applies the
        MINIMUM_BILLABLE_MINUTES floor rather than debiting nothing.
        """
        try:
            import av
            with av.open(local_path) as container:
                if container.duration is not None:
                    duration = float(container.duration) / 1_000_000.0  # AV_TIME_BASE = microseconds
                    if duration > 0:
                        return duration
                if container.streams.audio:
                    stream = container.streams.audio[0]
                    if stream.duration is not None and stream.time_base is not None:
                        duration = float(stream.duration * stream.time_base)
                        if duration > 0:
                            return duration
        except Exception as e:
            logger.debug(f"PyAV could not read audio duration for '{local_path}': {e}")

        if MutagenFile is not None:
            try:
                audio = MutagenFile(local_path)
                if audio is not None and audio.info is not None:
                    duration = float(audio.info.length)
                    if duration > 0:
                        logger.debug(f"Audio file '{local_path}' duration read via mutagen fallback: {duration:.2f}s")
                        return duration
            except Exception as e:
                logger.debug(f"Mutagen could not read audio duration for '{local_path}': {e}")

        logger.warning(
            f"Audio duration unreadable via both PyAV and mutagen for call_id={call_id}, "
            f"audio_url={audio_url!r} (local_path={local_path!r}); applying the "
            f"{MINIMUM_BILLABLE_MINUTES}-minute billing floor rather than a free debit."
        )
        return 0.0

    @staticmethod
    def _match_failed_line_offset(failed_line_text: Optional[str], transcript_chunks: List[Dict[str, Any]]) -> Optional[int]:
        """
        Calculates failure_offset_seconds by matching failed_line_text against transcript_chunks.
        Uses 2-stage matching:
        Stage 1: Normalized direct substring match.
        Stage 2: Token-set Jaccard overlap (threshold >= 0.5) to handle chunk boundary splits.
        """
        if not failed_line_text or not failed_line_text.strip() or not transcript_chunks:
            return None

        def norm(text: str) -> str:
            import re
            return re.sub(r'[^\w\s]', '', text).strip().lower()

        clean_failed = norm(failed_line_text)
        if not clean_failed:
            return None

        # Stage 1: Normalized direct substring match within a chunk
        for chunk in transcript_chunks:
            chunk_text_norm = norm(chunk.get("text", ""))
            if clean_failed in chunk_text_norm:
                return int(round(chunk.get("start_time", 0.0)))

        # Stage 2: Token-set overlap ratio for boundary splits
        failed_tokens = set(clean_failed.split())
        if not failed_tokens:
            return None

        best_chunk = None
        best_score = 0.0

        for chunk in transcript_chunks:
            chunk_tokens = set(norm(chunk.get("text", "")).split())
            if not chunk_tokens:
                continue
            intersection = failed_tokens.intersection(chunk_tokens)
            score = len(intersection) / float(len(failed_tokens))
            if score > best_score:
                best_score = score
                best_chunk = chunk

        if best_chunk is not None and best_score >= 0.5:
            return int(round(best_chunk.get("start_time", 0.0)))

        logger.debug(f"Failed line text '{failed_line_text}' could not be matched with >=50% token overlap. Defaulting failure_offset_seconds to None.")
        return None

    @staticmethod
    def _run_evaluation_pipeline(call_id: int, org: Any, dept: Any, audio_path: str) -> Dict[str, Any]:
        """Shared STT + LLM evaluation pipeline used by both single-upload and CSV batch flows."""
        existing_call = Call.get_by_id(call_id)
        existing_audio_url = existing_call["audio_url"] if existing_call else None
        duration_seconds = CallsController._get_audio_duration_seconds(audio_path, call_id=call_id, audio_url=existing_audio_url)
        if duration_seconds <= 0.0:
            if existing_call and existing_call["duration_seconds"]:
                duration_seconds = float(existing_call["duration_seconds"])
            if duration_seconds <= 0.0:
                # §2.8 floor: never write a 0.0-minute usage entry for a completed call.
                duration_seconds = MINIMUM_BILLABLE_MINUTES * 60.0

        logger.info(f"Pipeline Execution: Starting STT for call_id={call_id}")
        stt_result = STTService.transcribe(audio_path)
        transcript = stt_result.get("transcript", "")
        transcript_chunks = stt_result.get("transcript_chunks", [])
        stt_model = stt_result.get("model_used", "saaras:v3")
        logger.info(f"Pipeline Execution: STT completed for call_id={call_id}. Transcript length: {len(transcript)} chars, chunks: {len(transcript_chunks)}")

        if not transcript or not transcript.strip():
            err_msg = "Transcription is blank or empty"
            logger.warning(f"Pipeline Execution: Skipping LLM evaluation for call_id={call_id}: {err_msg}")
            Call.update_evaluation_results(
                call_id=call_id,
                transcript="",
                duration_seconds=duration_seconds,
                total_checked=0,
                total_passed=0,
                compliance_score_percentage=None,
                procedure_enquired="N/A",
                runtime_stt_model=stt_model,
                processing_status="failed",
                error_message=err_msg
            )
            return {
                "procedure_enquired": "N/A",
                "compliance_score_percentage": None,
                "processing_status": "failed",
                "error_message": err_msg
            }

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
        llm_provider = org["llm_provider"] or "openrouter"
        llm_effort = org["call_eval_effort"] or "medium"
        evaluation_result = LLMService.evaluate(
            model=llm_model,
            company_context=org["company_context"],
            department_context=dept["department_context"],
            parameters=llm_params,
            transcript=transcript,
            provider=llm_provider,
            effort=llm_effort
        )
        procedure_enquired = evaluation_result.get("procedure_enquired", "General Inquiry")
        eval_items = evaluation_result.get("evaluations", [])
        passed_count = sum(1 for item in eval_items if item.get("did_follow_rule") == 1)
        total_checked = len(eval_items)
        score = (passed_count / total_checked * 100.0) if total_checked > 0 else None

        logger.info(f"Pipeline Execution: LLM evaluation done for call_id={call_id}. Score: {f'{score:.1f}%' if score is not None else 'N/A'} ({passed_count}/{total_checked} passed)")

        evaluations_to_save = []
        for item in eval_items:
            param_match = next((p for p in active_params if p["id"] == item["parameter_id"]), None)
            snapshot_text = param_match["rule_description"] if param_match else ""
            did_follow = item.get("did_follow_rule", 1)
            failed_line = item.get("failed_line_text")
            offset_sec = CallsController._match_failed_line_offset(failed_line, transcript_chunks) if did_follow == 0 else None
            evaluations_to_save.append({
                "call_id": call_id,
                "parameter_id": item["parameter_id"],
                "did_follow_rule": did_follow,
                "failure_offset_seconds": offset_sec,
                "failure_reason": item.get("failure_reason"),
                "failed_line_text": failed_line,
                "parameter_snapshot_text": snapshot_text
            })
        if evaluations_to_save:
            CallEvaluation.create_batch(evaluations_to_save)

        stt_model = stt_result.get("model_used", "saaras:v3")
        llm_model_used = evaluation_result.get("model_used", llm_model)
        prompt_tokens = evaluation_result.get("prompt_tokens", 0)
        completion_tokens = evaluation_result.get("completion_tokens", 0)
        chunks_json = json.dumps(transcript_chunks) if transcript_chunks else None

        Call.update_evaluation_results(
            call_id=call_id,
            transcript=transcript,
            duration_seconds=duration_seconds,
            total_checked=total_checked,
            total_passed=passed_count,
            compliance_score_percentage=score,
            procedure_enquired=procedure_enquired,
            upstream_tokens_prompt=prompt_tokens,
            upstream_tokens_completion=completion_tokens,
            runtime_stt_model=stt_model,
            runtime_llm_model=llm_model_used,
            processing_status="completed",
            transcript_chunks=chunks_json
        )

        # Debit on completion, next to sync_daily_metrics_for_call (which
        # Call.update_evaluation_results already triggers above). Failed calls
        # are never charged — this only runs on the success path. Idempotent
        # via minute_ledger's unique index on call_id (Prepaid.debit_call).
        billable_minutes = round(duration_seconds / 60.0, 2)
        if billable_minutes <= 0.0:
            billable_minutes = MINIMUM_BILLABLE_MINUTES
        Prepaid.debit_call(organization_id=org["id"], call_id=call_id, minutes=billable_minutes)

        return {
            "procedure_enquired": procedure_enquired,
            "compliance_score_percentage": score
        }

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
        if calls:
            call_ids = [c["id"] for c in calls]
            eval_rows = CallEvaluation.list_by_call_ids(call_ids)
            eval_map: Dict[int, List[Dict[str, Any]]] = {}
            for r in eval_rows:
                r_dict = dict(r)
                cid = r_dict["call_id"]
                eval_map.setdefault(cid, []).append(r_dict)
            for c in calls:
                c["evaluations"] = eval_map.get(c["id"], [])
                if c.get("transcript_chunks") and isinstance(c["transcript_chunks"], str):
                    try:
                        c["transcript_chunks"] = json.loads(c["transcript_chunks"])
                    except Exception:
                        c["transcript_chunks"] = None
        logger.info(f"Retrieved {len(calls)} call records for user_id={current_user['id']} (effective org={effective_org_id}, dept={effective_dept_id})")
        return {"calls": calls}

    @staticmethod
    def get_call_details(current_user: Dict[str, Any], call_id: int) -> Dict[str, Any]:
        CallsController._verify_role(current_user, list(ROLES.values()))
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
        if call_dict.get("transcript_chunks") and isinstance(call_dict["transcript_chunks"], str):
            try:
                call_dict["transcript_chunks"] = json.loads(call_dict["transcript_chunks"])
            except Exception:
                call_dict["transcript_chunks"] = None
        logger.info(f"Retrieved call details for call_id={call_id} with {len(call_dict['evaluations'])} evaluations")
        return call_dict

    @staticmethod
    def get_export_data(current_user: Dict[str, Any], call_ids: List[int],
                        organization_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Fetches export call records (joined with agent name and department name)
        scoped strictly to caller's authorization. Rejects if any call_id is unauthorized.
        """
        CallsController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])
        effective_org_id = organization_id
        effective_dept_id = None

        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"], ROLES["agent"]]:
            effective_org_id = current_user["organization_id"]
        if current_user["role_id"] in [ROLES["manager"], ROLES["agent"]]:
            effective_dept_id = current_user["department_id"]
        if current_user["role_id"] == ROLES["superadmin"] and effective_org_id is None:
            logger.warning("Superadmin export data query missing organization_id filter.")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="organization_id is required when exporting calls as Superadmin."
            )

        if not call_ids:
            return {"calls": []}

        rows = Call.get_export_calls(call_ids)
        fetched_calls = [dict(r) for r in rows] if rows else []
        fetched_map = {c["id"]: c for c in fetched_calls}

        # Verify all requested call_ids exist and respect RBAC scoping
        for cid in call_ids:
            if cid not in fetched_map:
                logger.warning(f"Call record not found or inaccessible: call_id={cid}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Access denied for call_id={cid}")

            call_record = fetched_map[cid]
            if call_record["organization_id"] != effective_org_id:
                logger.warning(f"Cross-tenant call export denied for call_id={cid} to user_id={current_user['id']}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
            if effective_dept_id is not None and call_record["department_id"] != effective_dept_id:
                logger.warning(f"Cross-department call export denied for call_id={cid} to user_id={current_user['id']}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
            if current_user["role_id"] == ROLES["agent"] and call_record["user_id"] != current_user["id"]:
                logger.warning(f"Cross-agent call export denied for call_id={cid} to agent user_id={current_user['id']}")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

        # Batch fetch evaluations for all requested call_ids
        eval_rows = CallEvaluation.list_by_call_ids(call_ids)
        eval_map: Dict[int, List[Dict[str, Any]]] = {}
        for r in eval_rows:
            r_dict = dict(r)
            eval_item = {
                "parameter_name": r_dict.get("parameter_name"),
                "severity_level": r_dict.get("severity_level"),
                "did_follow_rule": r_dict.get("did_follow_rule"),
                "failure_offset_seconds": r_dict.get("failure_offset_seconds"),
                "failure_reason": r_dict.get("failure_reason"),
                "failed_line_text": r_dict.get("failed_line_text"),
            }
            eval_map.setdefault(r_dict["call_id"], []).append(eval_item)

        export_calls = []
        for cid in call_ids:
            c = fetched_map[cid]
            chunks_parsed = None
            if c.get("transcript_chunks") and isinstance(c["transcript_chunks"], str):
                try:
                    chunks_parsed = json.loads(c["transcript_chunks"])
                except Exception:
                    chunks_parsed = None
            export_item = {
                "id": c["id"],
                "created_at": c.get("created_at"),
                "transcript": c.get("transcript"),
                "transcript_chunks": chunks_parsed,
                "procedure_enquired": c.get("procedure_enquired"),
                "compliance_score_percentage": c.get("compliance_score_percentage"),
                "department_id": c["department_id"],
                "department_name": c.get("department_name"),
                "user_id": c.get("user_id"),
                "agent_name": c.get("agent_name"),
                "evaluations": eval_map.get(cid, [])
            }
            export_calls.append(export_item)

        logger.info(f"Exported data for {len(export_calls)} calls for user_id={current_user['id']}")
        return {"calls": export_calls}


    @staticmethod
    def process_audio_csv(current_user: Dict[str, Any], file: UploadFile) -> Dict[str, Any]:
        """
        Parses a batch CSV file, validates rows synchronously, creates pending calls,
        and returns 202 response immediately while background worker processes calls.
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

        batch_org = Organization.get_by_id(batch_organization_id)
        if batch_org:
            CallsController._enforce_prepaid_balance(dict(batch_org))

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

        failed_count = 0
        for idx, row in enumerate(rows, 1):
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
                try:
                    CallsController._enforce_prepaid_balance(org_dict)
                except HTTPException:
                    logger.warning(f"CSV Row #{idx} skipped: organization org_id={org_id} prepaid balance is blocked")
                    failed_count += 1
                    CSVUpload.increment_progress(csv_upload_id, is_success=False)
                    continue

                Call.create(
                    organization_id=org_id,
                    department_id=dept_id,
                    user_id=u_id,
                    csv_upload_id=csv_upload_id,
                    audio_url=audio_path,
                    duration_seconds=0.0,
                    file_size_bytes=0
                )
            except Exception as e:
                logger.error(f"CSV Row #{idx} call enqueue error: {e}")
                failed_count += 1
                CSVUpload.increment_progress(csv_upload_id, is_success=False)

        batch_status = "processing"
        if failed_count == total_records:
            batch_status = "failed"
            CSVUpload.update_status(csv_upload_id, "failed")

        logger.info(f"CSV batch upload upload_id={csv_upload_id} accepted: queued rows={total_records - failed_count}, failed validation={failed_count}")
        return {
            "status": "success",
            "csv_upload_id": csv_upload_id,
            "total_records": total_records,
            "processed_records": 0,
            "failed_records": failed_count,
            "batch_status": batch_status,
            "message": "CSV batch upload accepted and queued for processing."
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

        filter_user_id = None
        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"], ROLES["agent"]]:
            filter_user_id = current_user["id"]

        rows = CSVUpload.list_by_organization(organization_id, user_id=filter_user_id)
        csv_uploads = [dict(row) for row in rows] if rows else []
        logger.info(f"Retrieved {len(csv_uploads)} CSV upload records for org_id={organization_id}, user_id={filter_user_id}")
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

    @staticmethod
    def reprocess_single_call(
        current_user: Dict[str, Any],
        call_id: int,
        mode: str = "full",
        department_id: Optional[int] = None,
        stt_model: Optional[str] = None,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_effort: Optional[str] = None
    ) -> Dict[str, Any]:
        """Superadmin method to re-run full pipeline, transcription only, or LLM evaluation only for a call."""
        CallsController._verify_role(current_user, [ROLES["superadmin"]])

        call = Call.get_by_id(call_id)
        if not call:
            logger.warning(f"Superadmin call reprocess failed: call_id={call_id} not found.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call record not found.")

        call_dict = dict(call)
        org = Organization.get_by_id(call_dict["organization_id"])
        if not org:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid organization mapping for call.")

        target_dept_id = department_id or call_dict["department_id"]
        dept = Department.get_by_id(target_dept_id)
        if not dept:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Department ID {target_dept_id} not found.")

        # Update department_id on call if Superadmin changed it
        if target_dept_id != call_dict["department_id"]:
            from src.app.models.base import DatabaseManager
            DatabaseManager.execute_update(
                "UPDATE calls SET department_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (target_dept_id, call_id)
            )
            call_dict["department_id"] = target_dept_id
            logger.info(f"Superadmin updated call_id={call_id} department mapping to department_id={target_dept_id}")

        org_dict = dict(org)
        dept_dict = dict(dept)

        effective_stt_model = stt_model or org_dict.get("stt_model_routing") or "saaras:v3"
        effective_llm_provider = llm_provider or org_dict.get("llm_provider") or "openrouter"
        effective_llm_model = llm_model or org_dict.get("llm_model_routing") or "openrouter/free"
        effective_llm_effort = llm_effort or org_dict.get("call_eval_effort") or "medium"

        audio_url = call_dict["audio_url"]
        local_temp_path = None

        try:
            # Handle audio file retrieval for STT if needed
            if mode in ["full", "transcription"]:
                if audio_url.startswith("http://") or audio_url.startswith("https://"):
                    os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
                    local_temp_path = os.path.join(TEMP_AUDIO_DIR, f"reprocess_{call_id}_{uuid.uuid4().hex[:8]}.mp3")
                    with httpx.Client(timeout=30.0) as client:
                        resp = client.get(audio_url)
                        resp.raise_for_status()
                        with open(local_temp_path, "wb") as f:
                            f.write(resp.content)
                    audio_target_path = local_temp_path
                else:
                    audio_target_path = audio_url

                logger.info(f"Superadmin Reprocess [{mode}]: Transcribing call_id={call_id} using STT model '{effective_stt_model}'")
                stt_result = STTService.transcribe(audio_target_path)
                transcript = stt_result.get("transcript", "")
                stt_model_used = stt_result.get("model_used", effective_stt_model)

                # Persist the transcript immediately, before any LLM evaluation is
                # attempted. STT is billable work; if the LLM step below fails
                # (e.g. provider quota exhaustion) we must not discard it.
                from src.app.models.base import DatabaseManager
                DatabaseManager.execute_update(
                    """
                    UPDATE calls SET transcript = ?, runtime_stt_model = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;
                    """,
                    (transcript, stt_model_used, call_id)
                )
            else:
                transcript = call_dict.get("transcript", "")
                stt_model_used = call_dict.get("runtime_stt_model")

            if not transcript or not transcript.strip():
                err_msg = "Transcription is blank or empty"
                logger.warning(f"Superadmin Reprocess [{mode}]: Call call_id={call_id} transcription is blank/empty.")
                Call.update_evaluation_results(
                    call_id=call_id,
                    transcript="",
                    duration_seconds=float(call_dict.get("duration_seconds") or 0.0),
                    total_checked=0,
                    total_passed=0,
                    compliance_score_percentage=None,
                    procedure_enquired="N/A",
                    runtime_stt_model=stt_model_used,
                    runtime_llm_model=call_dict.get("runtime_llm_model"),
                    processing_status="failed",
                    error_message=err_msg
                )
                updated_call = Call.get_by_id(call_id)
                return {
                    "status": "failed",
                    "message": f"Call #{call_id} transcription is blank or empty.",
                    "call": dict(updated_call) if updated_call else {}
                }

            # Handle LLM evaluation if requested
            if mode in ["full", "llm"]:
                raw_params = ComplianceParameter.list_by_department(org_dict["id"], dept_dict["id"])
                active_params = [dict(p) for p in raw_params if p["is_active"] == 1] if raw_params else []
                llm_params = [
                    {
                        "id": p["id"],
                        "parameter_name": p["parameter_name"],
                        "rule_description": p["rule_description"],
                        "severity_level": p["severity_level"]
                    }
                    for p in active_params
                ]

                logger.info(f"Superadmin Reprocess [{mode}]: Evaluating call_id={call_id} against dept_id={dept_dict['id']} using LLM provider '{effective_llm_provider}', model '{effective_llm_model}', effort '{effective_llm_effort}'")
                try:
                    evaluation_result = LLMService.evaluate(
                        model=effective_llm_model,
                        company_context=org_dict.get("company_context"),
                        department_context=dept_dict.get("department_context"),
                        parameters=llm_params,
                        transcript=transcript,
                        provider=effective_llm_provider,
                        effort=effective_llm_effort
                    )
                except Exception as e:
                    # Full provider detail (quota, auth, model name, etc.) is logged
                    # server-side only. The transcript above is already saved, so a
                    # retry does not repeat the billable STT step.
                    logger.error(
                        f"Superadmin Reprocess [{mode}]: LLM evaluation failed for call_id={call_id} "
                        f"(provider='{effective_llm_provider}', model='{effective_llm_model}'): {e}"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Evaluation service is temporarily unavailable. Please try again shortly."
                    )

                procedure_enquired = evaluation_result.get("procedure_enquired", call_dict.get("procedure_enquired") or "General Inquiry")
                eval_items = evaluation_result.get("evaluations", [])
                passed_count = sum(1 for item in eval_items if item.get("did_follow_rule") == 1)
                total_checked = len(eval_items)
                score = (passed_count / total_checked * 100.0) if total_checked > 0 else None

                evaluations_to_save = []
                for item in eval_items:
                    param_match = next((p for p in active_params if p["id"] == item["parameter_id"]), None)
                    snapshot_text = param_match["rule_description"] if param_match else ""
                    evaluations_to_save.append({
                        "call_id": call_id,
                        "parameter_id": item["parameter_id"],
                        "did_follow_rule": item["did_follow_rule"],
                        "failure_offset_seconds": None,
                        "failure_reason": item.get("failure_reason"),
                        "failed_line_text": item.get("failed_line_text"),
                        "parameter_snapshot_text": snapshot_text
                    })

                CallEvaluation.replace_evaluations(call_id, evaluations_to_save)

                prompt_tokens = evaluation_result.get("prompt_tokens", call_dict.get("upstream_tokens_prompt", 0))
                completion_tokens = evaluation_result.get("completion_tokens", call_dict.get("upstream_tokens_completion", 0))
                llm_model_used = evaluation_result.get("model_used", effective_llm_model)

                Call.update_evaluation_results(
                    call_id=call_id,
                    transcript=transcript,
                    duration_seconds=float(call_dict.get("duration_seconds") or 0.0),
                    total_checked=total_checked,
                    total_passed=passed_count,
                    compliance_score_percentage=score,
                    procedure_enquired=procedure_enquired,
                    upstream_tokens_prompt=prompt_tokens,
                    upstream_tokens_completion=completion_tokens,
                    runtime_stt_model=stt_model_used,
                    runtime_llm_model=llm_model_used,
                    processing_status="completed"
                )
            else:
                # STT-only reprocess: update transcript and STT model without wiping evaluations
                from src.app.models.base import DatabaseManager
                DatabaseManager.execute_update(
                    """
                    UPDATE calls SET transcript = ?, runtime_stt_model = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;
                    """,
                    (transcript, stt_model_used, call_id)
                )

            updated_call = Call.get_by_id(call_id)
            logger.info(f"Superadmin call reprocess completed successfully: call_id={call_id}, mode='{mode}'")
            return {"status": "success", "message": f"Call #{call_id} reprocessed successfully ({mode}).", "call": dict(updated_call) if updated_call else {}}

        finally:
            if local_temp_path and os.path.exists(local_temp_path):
                try:
                    os.remove(local_temp_path)
                except Exception:
                    pass

    @staticmethod
    def reprocess_batch_calls(
        current_user: Dict[str, Any],
        call_ids: List[int],
        organization_id: int,
        mode: str = "full",
        department_id: Optional[int] = None,
        stt_model: Optional[str] = None,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_effort: Optional[str] = None
    ) -> Dict[str, Any]:
        """Superadmin method to batch reprocess multiple calls with rate limit error handling."""
        CallsController._verify_role(current_user, [ROLES["superadmin"]])

        if not call_ids:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="call_ids array cannot be empty.")

        successful_ids = []
        failed_ids = []
        errors = []

        for cid in call_ids:
            try:
                CallsController.reprocess_single_call(
                    current_user=current_user,
                    call_id=cid,
                    mode=mode,
                    department_id=department_id,
                    stt_model=stt_model,
                    llm_provider=llm_provider,
                    llm_model=llm_model,
                    llm_effort=llm_effort
                )
                successful_ids.append(cid)
            except Exception as e:
                logger.error(f"Error reprocessing call_id={cid} in batch: {e}")
                failed_ids.append(cid)
                errors.append(f"Call #{cid}: {str(e)}")

        logger.info(f"Superadmin batch reprocess completed: {len(successful_ids)} succeeded, {len(failed_ids)} failed")
        return {
            "status": "completed",
            "total": len(call_ids),
            "processed_records": len(successful_ids),
            "failed_records": len(failed_ids),
            "successful_call_ids": successful_ids,
            "failed_call_ids": failed_ids,
            "errors": errors
        }

    @staticmethod
    def manual_update_call(
        current_user: Dict[str, Any],
        call_id: int,
        procedure_enquired: Optional[str] = None,
        transcript: Optional[str] = None,
        evaluations: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Superadmin method to manually override call details, transcript, and rule evaluations."""
        CallsController._verify_role(current_user, [ROLES["superadmin"]])

        call = Call.get_by_id(call_id)
        if not call:
            logger.warning(f"Superadmin manual edit failed: call_id={call_id} not found.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call record not found.")

        call_dict = dict(call)
        new_procedure = procedure_enquired if procedure_enquired is not None else call_dict.get("procedure_enquired")
        new_transcript = transcript if transcript is not None else call_dict.get("transcript")

        total_checked = call_dict.get("total_parameters_checked", 0)
        total_passed = call_dict.get("total_parameters_passed", 0)
        score = call_dict.get("compliance_score_percentage")

        if evaluations is not None:
            formatted_evals = []
            for ev in evaluations:
                formatted_evals.append({
                    "call_id": call_id,
                    "parameter_id": ev["parameter_id"],
                    "did_follow_rule": ev.get("did_follow_rule", 1),
                    "failure_offset_seconds": ev.get("failure_offset_seconds"),
                    "failure_reason": ev.get("failure_reason"),
                    "failed_line_text": ev.get("failed_line_text"),
                    "parameter_snapshot_text": ev.get("parameter_snapshot_text", "")
                })

            CallEvaluation.replace_evaluations(call_id, formatted_evals)
            total_checked = len(formatted_evals)
            total_passed = sum(1 for e in formatted_evals if e.get("did_follow_rule") == 1)
            score = (total_passed / total_checked * 100.0) if total_checked > 0 else None

        target_status = call_dict.get("processing_status", "completed")
        if target_status == "failed" and (new_transcript or evaluations is not None):
            target_status = "completed"

        Call.update_evaluation_results(
            call_id=call_id,
            transcript=new_transcript or "",
            duration_seconds=float(call_dict.get("duration_seconds") or 0.0),
            total_checked=total_checked,
            total_passed=total_passed,
            compliance_score_percentage=score,
            procedure_enquired=new_procedure,
            upstream_tokens_prompt=call_dict.get("upstream_tokens_prompt", 0),
            upstream_tokens_completion=call_dict.get("upstream_tokens_completion", 0),
            runtime_stt_model=call_dict.get("runtime_stt_model"),
            runtime_llm_model=call_dict.get("runtime_llm_model"),
            processing_status=target_status,
            error_message=None if target_status == "completed" else call_dict.get("error_message")
        )

        updated_call = CallsController.get_call_details(current_user=current_user, call_id=call_id)
        logger.info(f"Superadmin manual call update successful for call_id={call_id}")
        return {"status": "success", "message": f"Call #{call_id} updated successfully.", "call": updated_call}


