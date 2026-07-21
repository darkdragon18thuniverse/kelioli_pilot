import os
import csv
import io
import httpx
from typing import Dict, Any, List
from fastapi import HTTPException, status, UploadFile

from src.app.models.base import DatabaseManager
from src.app.models.compliance import ComplianceParameter
from src.app.services.stt import download_and_transcribe_call

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

class CallsController:
    """
    Manages CSV log stream parsing, multi-threaded transcribing handshakes,
    and structured OpenRouter evaluation injections.
    """

    @staticmethod
    def process_audio_csv(current_user: Dict[str, Any], file: UploadFile) -> Dict[str, Any]:
        """
        Parses an incoming batch file, extracts target links, downloads audio, 
        requests Sarvam speech text, and updates core compliance indices.
        """
        # 1. Enforce strict multi-tenant context validation boundaries
        org_id = current_user.get("organization_id")
        dept_id = current_user.get("department_id")
        user_id = current_user.get("id")

        if not org_id or not dept_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Users missing target Organization or Department scopes cannot process data."
            )

        # 2. Extract configuration route tags for models from target Organization framework
        org_rows = DatabaseManager.execute_query(
            "SELECT stt_model_routing, llm_model_routing FROM organizations WHERE id = ?;", (org_id,)
        )
        if not org_rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target tenant organization not found.")
        
        stt_model = org_rows[0]["stt_model_routing"]
        llm_model = org_rows[0]["llm_model_routing"]

        # 3. Read raw CSV layout from multi-part file binary payload memory
        try:
            contents = file.file.read().decode("utf-8")
            csv_reader = csv.DictReader(io.StringIO(contents))
            rows = [row for row in csv_reader if row.get("audio_url")]
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail=f"Failed to cleanly read uploaded CSV structure: {str(e)}"
            )

        if not rows:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target CSV does not contain valid audio_url tracking items.")

        # 4. Initialize the tracking parent batch database record
        csv_upload_id = DatabaseManager.execute_update(
            """
            INSERT INTO csv_uploads (organization_id, user_id, filename, total_records, processed_records, failed_records, status)
            VALUES (?, ?, ?, ?, 0, 0, 'processing');
            """,
            (org_id, user_id, file.filename, len(rows))
        )

        processed_count = 0
        failed_count = 0

        # 5. Fetch dynamic check guidelines from matching sandbox domain
        active_rules = ComplianceParameter.list_active_by_scope(organization_id=org_id, department_id=dept_id)

        # Instantiate persistent network pipeline client context
        with httpx.Client(timeout=60.0) as network_client:
            for row in rows:
                audio_url = row["audio_url"].strip()
                call_id = None
                
                try:
                    # Pre-flight initialization tracking record block entry
                    call_id = DatabaseManager.execute_update(
                        """
                        INSERT INTO calls (organization_id, department_id, user_id, csv_upload_id, audio_url, processing_status)
                        VALUES (?, ?, ?, ?, ?, 'transcribing');
                        """,
                        (org_id, dept_id, user_id, csv_upload_id, audio_url)
                    )

                    # Stage A: File Stream Verification Pass
                    file_size = 0
                    try:
                        with network_client.stream("GET", audio_url) as head_check:
                            if head_check.status_code == 200:
                                file_size = int(head_check.headers.get("Content-Length", 0))
                    except Exception:
                        pass # Non-blocking sizing extraction fallback tracking log

                    # Stage B: Perform Sarvam Engine Text Transformation
                    transcript_text = download_and_transcribe_call(audio_url, network_client)
                    
                    DatabaseManager.execute_update(
                        "UPDATE calls SET transcript = ?, processing_status = 'evaluating' WHERE id = ?;",
                        (transcript_text, call_id)
                    )

                    # Stage C: Structured OpenRouter Audit Verification Engine Run
                    passed_parameters_count = 0
                    procedure_enquired = "General Consultation"
                    prompt_tokens, completion_tokens, computed_cost = 0, 0, 0.0

                    if active_rules and OPENROUTER_API_KEY:
                        eval_json, prompt_tokens, completion_tokens = CallsController._evaluate_transcript_via_llm(
                            transcript=transcript_text,
                            rules=active_rules,
                            model_routing=llm_model,
                            network_client=network_client
                        )
                        
                        procedure_enquired = eval_json.get("procedure_enquired", "General Consultation")
                        evaluations_list = eval_json.get("evaluations", [])

                        # Map the evaluation list context arrays into granular database table components
                        for rule in active_rules:
                            match_eval = next((e for e in evaluations_list if e.get("parameter_id") == rule["id"]), None)
                            
                            did_follow = 1
                            offset = None
                            reason = None
                            
                            if match_eval:
                                did_follow = 1 if match_eval.get("did_follow_rule") is True else 0
                                offset = match_eval.get("failure_offset_seconds")
                                reason = match_eval.get("failure_reason")
                            
                            if did_follow == 1:
                                passed_parameters_count += 1

                            DatabaseManager.execute_update(
                                """
                                INSERT INTO call_evaluations (call_id, parameter_id, did_follow_rule, failure_offset_seconds, failure_reason, parameter_snapshot_text)
                                VALUES (?, ?, ?, ?, ?, ?);
                                """,
                                (call_id, rule["id"], did_follow, offset, reason, rule["rule_description"])
                            )

                    # Calculate total metrics values
                    total_checked = len(active_rules)
                    score_pct = (passed_parameters_count / total_checked * 100.0) if total_checked > 0 else 100.0

                    # Write back full historical runtime metrics logs into rows
                    DatabaseManager.execute_update(
                        """
                        UPDATE calls 
                        SET file_size_bytes = ?, procedure_enquired = ?, processing_status = 'completed',
                            runtime_stt_model = ?, runtime_llm_model = ?, upstream_tokens_prompt = ?, 
                            upstream_tokens_completion = ?, internal_execution_cost = ?, 
                            total_parameters_checked = ?, total_parameters_passed = ?, compliance_score_percentage = ?
                        WHERE id = ?;
                        """,
                        (file_size, procedure_enquired, stt_model, llm_model, prompt_tokens, 
                         completion_tokens, computed_cost, total_checked, passed_parameters_count, score_pct, call_id)
                    )
                    processed_count += 1

                except Exception as row_error:
                    failed_count += 1
                    error_msg = str(row_error)
                    if call_id:
                        DatabaseManager.execute_update(
                            "UPDATE calls SET processing_status = 'failed', error_message = ? WHERE id = ?;",
                            (error_msg, call_id)
                        )

            # 6. Finalize Batch Framework State Transitions
            final_status = "completed" if failed_count == 0 else "failed" if processed_count == 0 else "completed"
            DatabaseManager.execute_update(
                """
                UPDATE csv_uploads 
                SET processed_records = ?, failed_records = ?, status = ? 
                WHERE id = ?;
                """,
                (processed_count, failed_count, final_status, csv_upload_id)
            )

        return {
            "status": final_status,
            "csv_upload_id": csv_upload_id,
            "total_records": len(rows),
            "processed": processed_count,
            "failed": failed_count
        }

    @staticmethod
    def _evaluate_transcript_via_llm(transcript: str, rules: List[Any], model_routing: str, network_client: httpx.Client) -> tuple:
        """Helper to invoke OpenRouter with structural JSON parsing directives."""
        
        # Build systematic rules payload schema injection context string
        rules_payload_string = ""
        for r in rules:
            rules_payload_string += f"- [Rule ID: {r['id']}] Name: {r['parameter_name']}\n  Criteria: {r['rule_description']}\n"

        system_instruction = (
            "You are a strict healthcare operational auditing validator engine.\n"
            "Analyze the provided medical transcript text against the given playbook rules mapping.\n"
            "You must output a valid JSON object matching this exact structural interface:\n"
            "{\n"
            '  "procedure_enquired": "string description of medical service requested",\n'
            '  "evaluations": [\n'
            "    {\n"
            '      "parameter_id": integer,\n'
            '      "did_follow_rule": boolean,\n'
            '      "failure_offset_seconds": integer or null,\n'
            '      "failure_reason": "string or null"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        user_content = f"--- PLAYBOOK COMPLIANCE RULES ---\n{rules_payload_string}\n\n--- TRANSCRIPT CONTENT ---\n{transcript}"

        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model_routing,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1
        }

        try:
            resp = network_client.post(OPENROUTER_URL, headers=headers, json=payload)
            if resp.status_code != 200:
                return {}, 0, 0
            
            res_data = resp.json()
            choices = res_data.get("choices", [])
            if not choices:
                return {}, 0, 0
                
            content_str = choices[0]["message"]["content"]
            
            # Extract billing usage data tokens from OpenRouter metadata response maps
            usage = res_data.get("usage", {})
            prompt_t = usage.get("prompt_tokens", 0)
            comp_t = usage.get("completion_tokens", 0)

            import json
            return json.loads(content_str), prompt_t, comp_t
        except Exception:
            return {}, 0, 0